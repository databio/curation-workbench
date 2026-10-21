# SNAPGATE module - do not edit. Overwritten on updates. Customize via SnapgateHooks.
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import ClassVar

from sqlalchemy import JSON, Column, DateTime
from sqlmodel import Field, Relationship, SQLModel


class UserStatus(str, Enum):
    """User account status for approval workflows."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    SUSPENDED = "suspended"


class User(SQLModel, table=True):
    """Core user table for authentication."""

    __tablename__ = "snapgate_users"

    id: int | None = Field(default=None, primary_key=True)
    # ``issuer#subject`` (see ``sign_in_oidc_user``). The name is historical:
    # rows made before snapgate spoke generic OIDC hold a bare Google ``sub``
    # until OIDC_ADOPT_CLAIM re-keys them.
    google_id: str = Field(unique=True, index=True)
    email: str = Field(unique=True, index=True)
    name: str
    picture: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True)),
    )
    last_login: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True)),
    )

    # User status (replaces is_approved and is_active)
    status: UserStatus = Field(default=UserStatus.PENDING)
    approved_by: int | None = Field(default=None, foreign_key="snapgate_users.id")
    approved_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))

    # Relationships
    refresh_tokens: list["RefreshToken"] = Relationship(back_populates="user")
    api_keys: list["ApiKey"] = Relationship(back_populates="user")


class RefreshToken(SQLModel, table=True):
    """Opaque refresh tokens with rotation tracking.

    Only the sha256 of the token is stored; the token itself is returned once,
    when made.
    """

    __tablename__ = "refresh_tokens"

    id: int | None = Field(default=None, primary_key=True)
    token_hash: str = Field(unique=True, index=True)
    user_id: int = Field(foreign_key="snapgate_users.id", index=True)
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True)),
    )

    # Single-use tracking (replaces timing-based detection)
    used: bool = Field(default=False, index=True)
    used_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    revoked: bool = Field(default=False)

    # Token rotation tracking
    token_family: str = Field(default_factory=lambda: secrets.token_urlsafe(16), index=True)
    parent_token_id: int | None = Field(default=None, foreign_key="refresh_tokens.id")

    # Security metadata
    created_from_ip: str | None = None
    last_used_from_ip: str | None = None
    user_agent: str | None = None

    # Relationship
    user: User | None = Relationship(back_populates="refresh_tokens")

    @property
    def is_expired(self) -> bool:
        now = datetime.now(timezone.utc)
        expires = self.expires_at
        # Handle naive datetimes (databases without TIMESTAMPTZ)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return now > expires

    @property
    def is_valid(self) -> bool:
        return not self.revoked and not self.is_expired and not self.used


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(secret: str) -> str:
    """Refresh tokens and one-use codes are 256-bit random values: sha256 is enough."""
    return hashlib.sha256(secret.encode()).hexdigest()


class OAuthConnection(SQLModel, table=True):
    """One chat app's standing access to one person's account (see oauth.py).

    Holds the connection's current refresh token (its sha256 hash) and the
    scopes the person chose. Tokens it rotated away live in
    ``OAuthRefreshHistory``. The access tokens are ordinary ``ApiKey`` rows
    pointing here through ``ApiKey.connection_id``.
    """

    __tablename__ = "oauth_connections"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="snapgate_users.id", index=True)
    client_id: str = Field(index=True)
    client_name: str
    # The website that vouches for a URL client_id (a metadata document); None
    # for a registered client.
    client_host: str | None = None
    # Set at approval; changed only by the owner (PATCH /auth/connections/{id}),
    # never by a refresh.
    scopes: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False, server_default="[]")
    )
    refresh_hash: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True)))
    last_refreshed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    # Slides forward on each refresh; the connection lapses after this.
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), index=True))
    revoked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class ApiKey(SQLModel, table=True):
    """API keys for programmatic/headless authentication."""

    __tablename__ = "api_keys"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="snapgate_users.id", index=True)

    # Key metadata
    name: str  # Human-readable name (e.g., "MCP Server", "CI/CD")
    description: str | None = None

    # Hashed key (never store plaintext)
    key_hash: str  # bcrypt hash of the API key
    prefix: str = Field(
        index=True
    )  # First 16 chars for efficient lookup (e.g., "snapgate_xQJ4abc")

    # Lifecycle
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True)),
    )
    last_used_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    expires_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )  # Optional expiration

    # Status
    revoked: bool = Field(default=False)

    # Opaque, app-defined scope strings, stored expanded. Empty for apps that
    # declare no scopes.
    scopes: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False, server_default="[]")
    )
    # When set, the key works only on requests under this path (e.g. "/mcp").
    # Set only by the OAuth token endpoint.
    confined_to: str | None = None
    # The OAuth connection that minted this key; None for Settings-page keys.
    connection_id: int | None = Field(default=None, foreign_key="oauth_connections.id", index=True)

    # Relationship
    user: User | None = Relationship(back_populates="api_keys")

    @property
    def is_valid(self) -> bool:
        """Check if key is valid (not revoked, not expired)."""
        if self.revoked:
            return False
        if self.expires_at:
            now = datetime.now(timezone.utc)
            expires = self.expires_at
            # Handle naive datetimes (databases without TIMESTAMPTZ)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if now > expires:
                return False
        return True


class ExchangeCode(SQLModel, table=True):
    """Short-lived, single-use code handed to the SPA after sign-in.

    Only the sha256 of the code is stored. The row only proves "this person
    just signed in"; the access and refresh tokens are minted at
    ``/auth/exchange``.
    """

    __tablename__ = "exchange_codes"

    id: int | None = Field(default=None, primary_key=True)
    code_hash: str = Field(unique=True, index=True)
    user_id: int = Field(foreign_key="snapgate_users.id")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True)),
    )
    used: bool = Field(default=False)

    EXPIRY_SECONDS: ClassVar[int] = 30

    @property
    def is_expired(self) -> bool:
        now = datetime.now(timezone.utc)
        created = self.created_at
        # Handle naive datetimes (databases without TIMESTAMPTZ)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return now > created + timedelta(seconds=self.EXPIRY_SECONDS)


class OAuthClient(SQLModel, table=True):
    """A chat app registered through RFC 7591 (public client, no secret).

    Apps identified by a Client ID Metadata Document have no row.
    """

    __tablename__ = "oauth_clients"

    id: int | None = Field(default=None, primary_key=True)
    client_id: str = Field(unique=True, index=True)
    client_name: str
    redirect_uris: list[str] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True)))


class OAuthCode(SQLModel, table=True):
    """A single-use authorization code (sha256-hashed), good for a few minutes."""

    __tablename__ = "oauth_codes"

    id: int | None = Field(default=None, primary_key=True)
    code_hash: str = Field(unique=True, index=True)
    client_id: str = Field(index=True)
    client_name: str
    client_host: str | None = None
    user_id: int = Field(foreign_key="snapgate_users.id", index=True)
    scopes: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False, server_default="[]")
    )
    redirect_uri: str
    code_challenge: str
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True)))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), index=True))
    used_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    # Set when this code minted a connection; a later presentation of the
    # same code revokes it (RFC 6749 section 4.1.2).
    connection_id: int | None = Field(
        default=None, foreign_key="oauth_connections.id", index=True, ondelete="SET NULL"
    )


class OAuthRefreshHistory(SQLModel, table=True):
    """A refresh token a connection has rotated away (sha256 only).

    Presented inside ``grace_until``: an overlapping refresh, still honored.
    Presented after: a replay, which revokes the connection.
    """

    __tablename__ = "oauth_refresh_history"

    id: int | None = Field(default=None, primary_key=True)
    connection_id: int = Field(foreign_key="oauth_connections.id", index=True, ondelete="CASCADE")
    token_hash: str = Field(unique=True, index=True)
    retired_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    grace_until: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
