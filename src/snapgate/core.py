# SNAPGATE module - do not edit. Overwritten on updates. Customize via SnapgateHooks.
"""
Snapgate Core - All authentication business logic consolidated.

This module contains all the core functionality for Snapgate authentication:
- Settings and configuration
- Exception types
- JWT token handling
- OpenID Connect sign-in through one issuer (via authlib, with PKCE, state and nonce)
- Authentication service
- FastAPI dependencies
- Rate limiting
- Router factory function

Snapgate uses header-based authentication (Authorization: Bearer <token>).
This works for both same-domain and cross-domain deployments.

NOTE: Token rotation uses a sync-in-threadpool pattern (_rotate_refresh_token_sync
wrapped by rotate_refresh_token) because SQLModel's Session uses psycopg2's blocking
driver, which cannot be used directly in async code. The split is intentional.
"""

import inspect
import logging
import secrets
import time
import warnings
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

import httpx

# authlib's OAuthError is aliased because snapgate defines its own
# `OAuthError(AuthError)` below, which would otherwise shadow it. Authlib's is the
# base class for every client-side OAuth failure it raises: MismatchingStateError,
# MissingTokenError, MissingRequestTokenError, InvalidTokenError, TokenExpiredError,
# UnsupportedTokenTypeError.
from authlib.integrations.starlette_client import OAuth, OAuthError as AuthlibOAuthError
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from limits import parse as parse_limit
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, or_, select
from starlette.concurrency import run_in_threadpool

from .config import settings
from .models import ExchangeCode, RefreshToken, User, UserStatus, hash_token

if TYPE_CHECKING:
    from .scopes import ScopeConfig

logger = logging.getLogger(__name__)


def _jose_errors() -> tuple[type[Exception], ...]:
    """What authlib raises for an id_token that fails verification.

    Signature, iss, aud, exp or nonce: a JOSE error, not an OAuthError. It is
    joserfc's on newer authlib and authlib.jose's (deprecated there) on older.
    """
    found: list[type[Exception]] = []
    try:
        from joserfc.errors import JoseError as RFCJoseError

        found.append(RFCJoseError)
    except ImportError:
        pass
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            from authlib.jose.errors import JoseError as AuthlibJoseError

            found.append(AuthlibJoseError)
        except ImportError:
            pass
    return tuple(found)


_ID_TOKEN_ERRORS = _jose_errors()


def _http_errors() -> tuple[type[Exception], ...]:
    """What authlib raises when the issuer is unreachable or answers an error.

    httpx's errors on older authlib; httpx2's on newer authlib, which moved to
    it (a 404 discovery document there is ``httpx2.HTTPStatusError``).
    """
    found: list[type[Exception]] = [httpx.HTTPError]
    try:
        import httpx2

        found.append(httpx2.HTTPError)
    except ImportError:
        pass
    return tuple(found)


_HTTP_ERRORS = _http_errors()

# =============================================================================
# LIFECYCLE HOOKS
# =============================================================================

# Type for hooks that can be sync or async
SyncHook = Callable[["User", Session], None]
AsyncHook = Callable[["User", Session], Awaitable[None]]
HookCallable = SyncHook | AsyncHook


@dataclass
class SnapgateHooks:
    """
    Optional callbacks for snapgate lifecycle events.

    Hooks can be sync or async functions. They receive (user, db) and return None.
    Exceptions in hooks are logged but don't break the auth flow.

    Hooks are called AFTER the database transaction is committed.
    The user object may be detached from the session.

    ``on_sign_in_attempt(db, attempt)`` is different: it is **sync only**, runs
    in a threadpool, and receives a :class:`SignInAttempt`. It runs inside the
    sign-in request AFTER ``on_user_created``/``on_login`` (so it sees any
    status they changed) and BEFORE any token or exchange code is made. It also
    runs for sign-ins that stopped at an ``AuthError`` before a user existed.
    snapgate commits after it returns. Like the others, a failure is logged
    and never breaks sign-in. ``snapgate.useradmin`` uses it for its sign-in log.
    """

    on_login: HookCallable | None = None
    on_user_created: HookCallable | None = None
    on_sign_in_attempt: "SignInAttemptHook | None" = None


@dataclass(frozen=True)
class SignInAttempt:
    """One sign-in that reached an identity, allowed or not (snapgate.useradmin logs it)."""

    email: str  # "" when the issuer sent none
    name: str | None
    user: "User | None"  # None when sign-in stopped before a user existed
    allowed: bool  # user.status == APPROVED after every hook ran
    reason: str | None  # None | "pending" | "denied" | "suspended" | AuthError.error_code
    ip: str | None  # client_ip(request)
    user_agent: str | None  # truncated to 512 chars
    method: Literal["oidc", "demo"]


SignInAttemptHook = Callable[[Session, SignInAttempt], None]


async def _call_hook(hook: HookCallable | None, user: "User", db: Session, hook_name: str):
    """Call a hook safely, handling both sync and async functions."""
    if not hook:
        return
    try:
        result = hook(user, db)
        if inspect.isawaitable(result):
            await result
    except Exception as e:
        logger.warning(f"{hook_name} hook failed: {e}")


async def _record_sign_in(hooks: "SnapgateHooks | None", db: Session, attempt: SignInAttempt):
    """Run ``on_sign_in_attempt`` and commit; a failure is logged, never raised."""
    if not hooks or not hooks.on_sign_in_attempt:
        return

    def run():
        try:
            hooks.on_sign_in_attempt(db, attempt)
            db.commit()
        except Exception as e:  # never break sign-in over a log row
            db.rollback()
            logger.warning(f"on_sign_in_attempt hook failed: {e}")

    await run_in_threadpool(run)


def _ua(request: Request) -> str | None:
    ua = request.headers.get("user-agent")
    return ua[:512] if ua else None


def _attempt_for(db: Session, user: "User", request: Request, method: str) -> SignInAttempt:
    """The attempt for a signed-in ``user``, after re-reading status earlier hooks may have set."""
    try:
        db.refresh(user)
    except Exception:  # a detached or deleted row: report what we hold
        logger.warning("Could not re-read user %s before logging sign-in", user.id)
    allowed = user.status == UserStatus.APPROVED
    return SignInAttempt(
        email=user.email,
        name=user.name,
        user=user,
        allowed=allowed,
        reason=None if allowed else user.status.value,
        ip=client_ip(request),
        user_agent=_ua(request),
        method=method,  # type: ignore[arg-type]
    )


def _user_json(user: "User", *extra: str) -> dict:
    """The ``user`` object of an auth response: the base fields, ``extra`` ones, and ``site_role``."""
    out = {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "status": user.status.value,
        "site_role": settings.site_role_for(user.email),
    }
    for name in extra:
        value = getattr(user, name)
        out[name] = value.isoformat() if isinstance(value, datetime) else value
    return out


# Settings are imported from config.py


# =============================================================================
# BEARER TOKEN PARSING
# =============================================================================


def _parse_bearer_token(authorization: str | None) -> str | None:
    """Extract token from 'Bearer <token>' authorization header."""
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1]


# =============================================================================
# TRANSACTION CONTEXT MANAGER
# =============================================================================


@contextmanager
def atomic_transaction(session: Session):
    """
    Context manager that commits on success, rolls back on exception.

    Usage:
        with atomic_transaction(self.db):
            # database operations
            # commit happens automatically on success
            # rollback happens automatically on exception

    This replaces the pattern:
        with self.db.begin_nested():
            # operations
        self.db.commit()  # easy to forget!
    """
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise


# =============================================================================
# EXCEPTION TYPES
# =============================================================================


class AuthError(HTTPException):
    """Base authentication error with consistent JSON formatting."""

    def __init__(
        self,
        user_message: str,
        log_message: str | None = None,
        status_code: int = 400,
        error_code: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        # Store for later use in exception handler
        self.error_code = error_code or "auth_error"
        self.user_message = user_message

        super().__init__(
            status_code=status_code,
            detail={"error": self.error_code, "message": user_message},
            headers=headers,
        )

        # Log the internal message (more detailed)
        if log_message:
            logger.error(f"Auth error [{self.error_code}]: {log_message}")


# Specific error types
class InvalidTokenError(AuthError):
    def __init__(self, log_message: str = "Invalid token"):
        super().__init__(
            user_message="Your session has expired. Please sign in again.",
            log_message=log_message,
            status_code=401,
            error_code="invalid_token",
        )


class UserNotApprovedError(AuthError):
    def __init__(self, email: str):
        super().__init__(
            user_message="Your account is pending approval. Please contact an administrator.",
            log_message=f"Unapproved user login attempt: {email}",
            status_code=403,
            error_code="user_not_approved",
        )


class OAuthError(AuthError):
    def __init__(self, provider: str, error_detail: str):
        super().__init__(
            user_message=f"Sign in with {provider} failed. Please try again.",
            log_message=f"OAuth error with {provider}: {error_detail}",
            status_code=400,
            error_code="oauth_error",
        )


# =============================================================================
# JWT TOKEN HANDLING
# =============================================================================


def _get_jwt_keys() -> tuple[str, str]:
    """Get appropriate keys based on algorithm."""
    if settings.ACCESS_TOKEN_ALGORITHM == "RS256":
        return settings.JWT_PRIVATE_KEY, settings.JWT_PUBLIC_KEY
    else:  # HS256
        return settings.JWT_SECRET_KEY, settings.JWT_SECRET_KEY


def create_access_token(user_id: int, email: str) -> tuple[str, datetime]:
    """
    Create a JWT access token for API requests.
    Returns: (token, expiration_datetime)
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    payload = {"sub": str(user_id), "email": email, "exp": expire, "iat": now, "type": "access"}

    private_key, _ = _get_jwt_keys()
    token = jwt.encode(payload, private_key, algorithm=settings.ACCESS_TOKEN_ALGORITHM)

    return token, expire


def verify_access_token(token: str) -> dict:
    """Verify and decode an access token."""
    try:
        _, public_key = _get_jwt_keys()
        payload = jwt.decode(token, public_key, algorithms=[settings.ACCESS_TOKEN_ALGORITHM])

        if payload.get("type") != "access":
            raise InvalidTokenError("Invalid token type")

        return payload

    except JWTError as e:
        logger.warning(f"Token verification failed: {str(e)}")
        raise InvalidTokenError(f"JWT verification failed: {str(e)}") from e


# =============================================================================
# REQUEST MODELS
# =============================================================================


class RefreshTokenRequest(BaseModel):
    """Request body for token refresh."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """Request body for logout."""

    refresh_token: str


class CreateApiKeyRequest(BaseModel):
    """Request body for API key creation."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    expires_days: int | None = Field(None, ge=1, le=365)
    # None: the app's default_scopes. Must be declared names; never empty.
    scopes: list[str] | None = None


class CodeExchangeRequest(BaseModel):
    """Request body for code exchange."""

    code: str


# =============================================================================
# OPENID CONNECT SIGN-IN (PKCE + STATE + NONCE)
# =============================================================================

# Snapgate signs people in through exactly one OpenID Connect issuer
# (OIDC_ISSUER) and never learns which provider is behind it. An app that needs
# a choice of providers points at a broker that offers them.
oauth = OAuth()
oauth.register(
    name="oidc",
    client_id=settings.OIDC_CLIENT_ID,
    client_secret=settings.OIDC_CLIENT_SECRET,
    server_metadata_url=f"{settings.OIDC_ISSUER}/.well-known/openid-configuration",
    client_kwargs={
        "scope": settings.OIDC_SCOPES,
        "code_challenge_method": "S256",  # Enable PKCE
    },
)


async def _oidc_client():
    """The issuer's client, once its discovery document names OIDC_ISSUER exactly.

    Every user key is ``OIDC_ISSUER#sub``; an issuer that answers under another
    name would silently change all of them, so sign-in refuses to run.
    Metadata loads lazily (on the first sign-in) and is cached by authlib.
    """
    client = oauth.oidc
    metadata = await client.load_server_metadata()
    if metadata.get("issuer") != settings.OIDC_ISSUER:
        raise AuthError(
            user_message="Sign-in is misconfigured. Please tell the site's admin.",
            log_message=f"Discovery issuer {metadata.get('issuer')!r} is not "
            f"OIDC_ISSUER {settings.OIDC_ISSUER!r}",
            status_code=500,
            error_code="issuer_mismatch",
        )
    return client


# =============================================================================
# AUTHENTICATION SERVICE
# =============================================================================


def _add_refresh_token(
    db: Session,
    *,
    user_id: int,
    client_ip: str | None,
    user_agent: str | None,
    token_family: str | None = None,
    parent_token_id: int | None = None,
) -> tuple[RefreshToken, str]:
    """Add (without committing) a refresh token row; return it and the token.

    Only the token's sha256 goes in the row. The plaintext is returned here
    and nowhere else. ``token_family`` is passed to the model only when set,
    so a fresh sign-in gets the model's fresh family.
    """
    token = secrets.token_urlsafe(32)
    row = RefreshToken(
        token_hash=hash_token(token),
        user_id=user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        created_from_ip=client_ip,
        user_agent=user_agent,
        parent_token_id=parent_token_id,
        **({"token_family": token_family} if token_family else {}),
    )
    db.add(row)
    return row, token


class AuthService:
    """Business logic for authentication operations."""

    def __init__(self, db: Session):
        self.db = db

    async def create_refresh_token(
        self, user_id: int, request: Request, token_family: str | None = None
    ) -> tuple[RefreshToken, str]:
        """Create a new refresh token for the user.

        Returns ``(row, token)``. The row holds only the token's sha256; the
        token itself is not recoverable later.
        """
        row, token = _add_refresh_token(
            self.db,
            user_id=user_id,
            client_ip=request.client.host,
            user_agent=request.headers.get("user-agent"),
            token_family=token_family,
        )
        self.db.commit()
        self.db.refresh(row)

        logger.info(f"Refresh token created for user {user_id}")
        return row, token

    def _rotate_refresh_token_sync(
        self, old_token: str, client_ip: str, user_agent: str
    ) -> tuple[User, str, str, datetime]:
        """
        Synchronous token rotation with atomic transaction.
        This runs in a threadpool to avoid blocking.
        Returns: (user, access_token, new_refresh_token, access_expires_at)
        """
        with atomic_transaction(self.db):
            # Find and lock the refresh token
            statement = (
                select(RefreshToken)
                .where(RefreshToken.token_hash == hash_token(old_token))
                .with_for_update()
            )

            existing_token = self.db.exec(statement).first()

            if not existing_token:
                # Diagnostic: detect JWT-formatted tokens that were never stored
                # JWTs have 3 dot-separated parts (header.payload.signature)
                if old_token.count(".") == 2:
                    logger.warning(
                        "Refresh token appears to be a JWT but has no database record - "
                        "was it created without database storage? This typically means "
                        "an endpoint created a JWT refresh token instead of using "
                        "AuthService.create_refresh_token()"
                    )
                raise InvalidTokenError("Refresh token not found")

            if not existing_token.is_valid:
                raise InvalidTokenError("Refresh token expired or revoked")

            # Mark old token as used (and revoked to prevent reuse)
            existing_token.used = True
            existing_token.used_at = datetime.now(timezone.utc)
            existing_token.last_used_from_ip = client_ip
            existing_token.revoked = True

            # Get user - snapgate doesn't check status here, just that user exists
            user = self.db.get(User, existing_token.user_id)
            if not user:
                raise InvalidTokenError("User not found")

            # Create new refresh token (same family)
            _, token_value = _add_refresh_token(
                self.db,
                user_id=user.id,
                client_ip=client_ip,
                user_agent=user_agent,
                token_family=existing_token.token_family,
                parent_token_id=existing_token.id,
            )
            self.db.flush()

            # Create access token (doesn't touch DB)
            access_token, access_expires = create_access_token(user.id, user.email)

            # Commit happens automatically via atomic_transaction on success

        logger.info(f"Token rotated for user {user.id}")
        return user, access_token, token_value, access_expires

    async def rotate_refresh_token(
        self, old_token: str, request: Request
    ) -> tuple[User, str, str, datetime]:
        """
        Rotate refresh token asynchronously using threadpool.
        Returns: (user, access_token, new_refresh_token, access_expires_at)
        """
        # Run the sync rotation in a threadpool to avoid blocking
        return await run_in_threadpool(
            self._rotate_refresh_token_sync,
            old_token,
            request.client.host,
            request.headers.get("user-agent", ""),
        )

    async def logout_user(self, refresh_token: str, user_id: int | None = None):
        """Logout user by revoking their refresh token.

        Args:
            refresh_token: The refresh token to revoke
            user_id: If provided, validates token belongs to this user
        """
        # Lock the row to prevent race with concurrent refresh
        statement = (
            select(RefreshToken)
            .where(RefreshToken.token_hash == hash_token(refresh_token))
            .with_for_update()
        )
        token = self.db.exec(statement).first()

        if not token:
            return  # Token doesn't exist, nothing to revoke

        # Validate ownership if user_id provided
        if user_id is not None and token.user_id != user_id:
            logger.warning(
                f"User {user_id} attempted to revoke token belonging to user {token.user_id}"
            )
            return  # Silently fail - don't reveal token ownership

        token.revoked = True
        self.db.commit()
        logger.info(f"User {token.user_id} logged out")


# =============================================================================
# EXCHANGE CODE CLEANUP
# =============================================================================


def cleanup_expired_codes(db: Session) -> int:
    """
    Remove expired and used exchange codes, OAuth authorization codes expired
    for longer than the OAuth server's ``code_reuse_window`` (one day by
    default; kept that long so a reused code can still revoke its connection),
    and OAuth connections revoked or lapsed for longer than ``access_key_keep``
    (one day by default), with their keys, codes and refresh-token history.

    Returns the number of rows deleted.
    """
    from .models import ApiKey, OAuthCode, OAuthConnection, OAuthRefreshHistory

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    # A single bulk DELETE, not SELECT-then-``db.delete(obj)`` per row. The ORM
    # loop asserted "expected to delete 1 row(s)" per object, so two callers
    # racing on the same stale rows (two workers at startup, two shutdown
    # hooks) made the loser raise StaleDataError. A set-based DELETE just
    # reports how many rows IT removed. ``synchronize_session=False`` skips the
    # in-session bookkeeping, which is correct here: these rows are garbage and
    # no caller holds them.
    statement = delete(ExchangeCode).where(
        or_(
            ExchangeCode.used.is_(True),
            ExchangeCode.created_at < cutoff,
        )
    )
    result = db.exec(statement, execution_options={"synchronize_session": False})
    deleted = result.rowcount or 0

    from . import oauth

    installed = oauth._installed
    now = datetime.now(timezone.utc)
    reuse_window = installed.code_reuse_window if installed else oauth.CODE_REUSE_WINDOW
    result = db.exec(
        delete(OAuthCode).where(col(OAuthCode.expires_at) <= now - reuse_window),
        execution_options={"synchronize_session": False},
    )
    deleted += result.rowcount or 0

    keep = installed.access_key_keep if installed else timedelta(days=1)
    dead_before = now - keep
    dead = select(OAuthConnection.id).where(
        or_(
            col(OAuthConnection.revoked_at) < dead_before,
            col(OAuthConnection.expires_at) < dead_before,
        )
    )
    # Children first, explicitly: SQLite does not enforce foreign keys by default.
    for child in (
        delete(ApiKey).where(col(ApiKey.connection_id).in_(dead)),
        delete(OAuthRefreshHistory).where(col(OAuthRefreshHistory.connection_id).in_(dead)),
        delete(OAuthCode).where(col(OAuthCode.connection_id).in_(dead)),
    ):
        result = db.exec(child, execution_options={"synchronize_session": False})
        deleted += result.rowcount or 0
    result = db.exec(
        delete(OAuthConnection).where(col(OAuthConnection.id).in_(dead)),
        execution_options={"synchronize_session": False},
    )
    deleted += result.rowcount or 0
    db.commit()
    return deleted


# =============================================================================
# FASTAPI DEPENDENCY FACTORIES
# =============================================================================


def create_auth_dependencies(get_db: Callable):
    """
    Create authentication dependencies with injected database dependency.

    Args:
        get_db: Database session dependency function

    Returns:
        Tuple of (get_current_user, require_user, require_approved_user) dependencies
    """

    def get_current_user(
        request: Request, authorization: str | None = Header(None), db: Session = Depends(get_db)
    ) -> User | None:
        """Get current user from access token in Authorization: Bearer header.

        Snapgate returns the authenticated user regardless of status.
        Apps handle authorization based on user.status.
        """
        token = _parse_bearer_token(authorization)
        if not token:
            return None

        try:
            payload = verify_access_token(token)
            user_id = int(payload["sub"])

            user = db.get(User, user_id)
            if not user:
                return None

            return user

        except (JWTError, InvalidTokenError, KeyError):
            return None

    def require_user(user: User | None = Depends(get_current_user)) -> User:
        """Require authenticated user (any status)."""
        if not user:
            raise InvalidTokenError("Authentication required")
        return user

    def require_approved_user(user: User = Depends(require_user)) -> User:
        """Require authenticated user with approved status."""
        if user.status != UserStatus.APPROVED:
            raise UserNotApprovedError(user.email)
        return user

    return get_current_user, require_user, require_approved_user


# =============================================================================
# RATE LIMITING
# =============================================================================


def client_ip(request: Request) -> str:
    """True client IP for rate-limit keying.

    Reads ``settings.RATE_LIMIT_CLIENT_IP_HEADER`` when configured (the proxy is
    responsible for overwriting it on every request), else falls back to the
    socket peer address.

    Only the FIRST comma-separated token is used, stripped: a forwarding header
    accumulates ``<client>, <proxy1>, <proxy2>`` left to right, so the leftmost
    entry is the original client. That makes the value spoofable if the header
    is NOT overwritten by a trusted proxy — hence the setting defaults to None
    and must be opted into per deployment, naming the proxy that sets it.
    """
    header = settings.RATE_LIMIT_CLIENT_IP_HEADER
    if header:
        raw = request.headers.get(header)
        if raw:
            first = raw.split(",")[0].strip()
            if first:
                return first
    return get_remote_address(request)


# Create limiter instance.
#
# Storage is slowapi's default: fixed-window counters held in this process.
# That is correct for a single-process deployment, which is what snapgate
# assumes — with several workers each keeps its own counters and the effective
# limit becomes (workers x RATE_LIMIT_PER_MINUTE).
limiter = Limiter(key_func=client_ip, enabled=settings.RATE_LIMIT_ENABLED)

# Define rate limits for different endpoints
auth_rate_limits = {
    "login": f"{settings.RATE_LIMIT_PER_MINUTE}/minute",
    "refresh": f"{settings.RATE_LIMIT_PER_MINUTE * 2}/minute",  # Allow more refresh attempts
    "logout": f"{settings.RATE_LIMIT_PER_MINUTE}/minute",
    "exchange": "10/minute",
    "oauth_register": f"{settings.RATE_LIMIT_PER_MINUTE}/minute",
    "oauth_token": f"{settings.RATE_LIMIT_PER_MINUTE * 2}/minute",
    "role_request": f"{settings.RATE_LIMIT_PER_MINUTE}/minute",
}
_PARSED_LIMITS = {name: parse_limit(value) for name, value in auth_rate_limits.items()}


def within_rate_limit(request: Request, name: str, bucket: str | None = None) -> bool:
    """Count this request against limit ``name`` on snapgate's limiter storage.

    Not slowapi's ``@limiter.limit`` decorator: that files limits by function
    name, so every ``create_snapgate_router`` call stacked one more copy and a
    process that built ~10 apps (one per test) answered the first request 429.
    ``bucket`` separates routes that share a limit (default: ``name``).
    """
    if not limiter.enabled:
        return True
    return limiter.limiter.hit(_PARSED_LIMITS[name], "snapgate", bucket or name, client_ip(request))


def enforce_rate_limit(request: Request, name: str, bucket: str | None = None) -> None:
    """Raise a 429 ``AuthError`` (``rate_limited``) if this request is over limit ``name``."""
    if not within_rate_limit(request, name, bucket):
        raise AuthError(
            user_message="Too many requests. Please wait a minute and try again.",
            status_code=429,
            error_code="rate_limited",
            headers={"Retry-After": "60"},
        )


# =============================================================================
# CONSENT SIGN-IN (OAuth server only)
# =============================================================================

#: The only paths a sign-in's ``next`` may return to: the OAuth consent page.
CONSENT_NEXT_PREFIX = "/oauth/authorize"


def safe_consent_next(next_url: str) -> str:
    """``next`` if it is a same-site consent-page path, else AuthError 400.

    An app's own consent sign-in route calls this on its ``next`` parameter
    before starting its login, so it can only ever return to the consent page.
    """
    parts = urlsplit(next_url)
    if (
        parts.scheme
        or parts.netloc
        or next_url.startswith("//")
        or "\\" in next_url
        or not parts.path.startswith(CONSENT_NEXT_PREFIX)
    ):
        raise AuthError(
            user_message="Invalid redirect URL",
            log_message=f"Rejected sign-in next={next_url!r}",
            error_code="invalid_redirect",
        )
    return next_url


def set_consent_user(request: Request, user: User) -> None:
    """Sign ``user`` in for the OAuth consent page only.

    This is the consent-only sign-in: a short-lived session marker read by
    ``snapgate.oauth`` alone, never an API credential, and spent by the
    consent decision. The caller must already have verified the person's
    identity (snapgate's sign-in callback, or an app's own login callback).
    """
    request.session["sg_consent_user"] = {"id": user.id, "at": int(time.time())}


def get_or_create_external_user(
    db: Session,
    *,
    issuer: str,
    subject: str,
    email: str,
    name: str,
    picture: str | None = None,
    email_verified: bool,
    status: UserStatus = UserStatus.PENDING,
) -> User:
    """The snapgate user for a person an app signed in itself (OIDC, SSO, hexclave).

    The identity key is ``issuer#subject``, stored in ``User.google_id`` (the
    column keeps its old name; rows made before snapgate spoke generic OIDC
    hold a bare Google ``sub`` until :func:`sign_in_oidc_user` adopts them).
    An existing row for that key is returned with ``name``, ``picture`` and ``last_login``
    refreshed; ``email`` and ``status`` are never changed. A new identity never
    claims an existing row by email: that is AuthError 409 ``identity_conflict``.
    An unverified email is AuthError 403 ``email_unverified``. New rows get
    ``status``.

    The caller must already have verified the identity. This fires no
    ``SnapgateHooks``: snapgate's own sign-in fires them in its router, and an
    app that owns its login runs its own code.
    """
    if not issuer or not subject:
        raise AuthError(user_message="Missing sign-in identity", error_code="invalid_identity")
    external_id = f"{issuer}#{subject}"
    now = datetime.now(timezone.utc)
    user = db.exec(select(User).where(User.google_id == external_id)).first()
    if user is not None:
        user.name = name
        user.picture = picture
        user.last_login = now
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    if db.exec(select(User).where(User.email == email)).first() is not None:
        raise AuthError(
            user_message="This email is already linked to a different sign-in. "
            "Use your original sign-in method.",
            log_message=f"External identity {external_id!r} matched existing email {email!r}",
            status_code=409,
            error_code="identity_conflict",
        )
    if not email_verified:
        raise AuthError(
            user_message="Your sign-in provider has not verified this email address.",
            log_message=f"External identity {external_id!r} has unverified email {email!r}",
            status_code=403,
            error_code="email_unverified",
        )
    user = User(
        google_id=external_id,
        email=email,
        name=name,
        picture=picture,
        status=status,
        approved_at=now if status == UserStatus.APPROVED else None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(f"New external user {email} created ({issuer})")
    return user


def _approve_env_admin(db: Session, user: User) -> None:
    """An email on SNAPGATE_*_EMAILS is APPROVED at every sign-in, whatever the row says."""
    if settings.site_role_for(user.email) and user.status != UserStatus.APPROVED:
        user.status = UserStatus.APPROVED
        user.approved_at = user.approved_at or datetime.now(timezone.utc)
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info("Approved env-listed admin %s", user.email)


def _sign_in_oidc_user_once(
    db: Session, claims: dict, issuer: str, adopt_claim: str | None
) -> tuple[User, bool]:
    subject = claims.get("sub") or ""
    key = f"{issuer}#{subject}"
    email = claims.get("email") or ""
    name = claims.get("name") or email
    picture = claims.get("picture")
    now = datetime.now(timezone.utc)

    # 1. Signed in before under this issuer.
    if subject and db.exec(select(User).where(User.google_id == key)).first() is not None:
        user = get_or_create_external_user(
            db,
            issuer=issuer,
            subject=subject,
            email=email,
            name=name,
            picture=picture,
            email_verified=bool(claims.get("email_verified")),
        )
        _approve_env_admin(db, user)
        return user, False

    # 2. Known under an old key the issuer vouches for (OIDC_ADOPT_CLAIM).
    #    Never by email.
    old_key = claims.get(adopt_claim) if adopt_claim else None
    if subject and isinstance(old_key, str) and old_key and "#" not in old_key:
        user = db.exec(select(User).where(User.google_id == old_key).with_for_update()).first()
        if user is not None:
            user.google_id = key
            user.name = name
            user.picture = picture
            user.last_login = now
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info(f"Adopted user {user.id} from {adopt_claim} to {issuer}")
            _approve_env_admin(db, user)
            return user, False

    # 3. A new person. The first user ever is approved (no admin power; site
    #    roles come only from SNAPGATE_*_EMAILS); FOR UPDATE holds until the
    #    helper commits.
    if not email:
        raise AuthError(
            user_message="Your sign-in did not include an email address.",
            log_message=f"Identity {key!r} has no email",
            status_code=403,
            error_code="email_missing",
        )
    first = db.exec(select(User).limit(1).with_for_update()).first()
    status = UserStatus.APPROVED if first is None else UserStatus.PENDING
    user = get_or_create_external_user(
        db,
        issuer=issuer,
        subject=subject,
        email=email,
        name=name,
        picture=picture,
        email_verified=bool(claims.get("email_verified")),
        status=status,
    )
    if status == UserStatus.APPROVED:
        logger.info(f"First user {email} auto-approved")
    _approve_env_admin(db, user)
    return user, True


def sign_in_oidc_user(
    db: Session,
    claims: dict,
    *,
    issuer: str | None = None,
    adopt_claim: str | None = None,
) -> tuple[User, bool]:
    """The user for verified id_token ``claims``, and whether they are new.

    In order: the row keyed ``issuer#sub``; else, with ``adopt_claim`` set
    (default ``OIDC_ADOPT_CLAIM``) and present in the claims, the row whose
    ``google_id`` equals that claim's value, re-keyed to ``issuer#sub``; else
    a new user through :func:`get_or_create_external_user` (its email-conflict
    409 and unverified-email 403 apply). Never matches by email.

    Two first sign-ins at once can both miss and collide on the unique key or
    email; the loser rolls back and looks again once.
    """
    issuer = issuer or settings.OIDC_ISSUER
    if adopt_claim is None:
        adopt_claim = settings.OIDC_ADOPT_CLAIM
    if not claims.get("sub"):
        raise AuthError(user_message="Missing sign-in identity", error_code="invalid_identity")
    try:
        return _sign_in_oidc_user_once(db, claims, issuer, adopt_claim)
    except IntegrityError:
        db.rollback()
    try:
        return _sign_in_oidc_user_once(db, claims, issuer, adopt_claim)
    except IntegrityError as e:
        db.rollback()
        raise AuthError(
            user_message="Sign-in raced another change. Please try again.",
            log_message=f"Sign-in for {issuer}#{claims.get('sub')} collided twice",
            status_code=409,
            error_code="sign_in_raced",
        ) from e


def _demo_user(db: Session, info: dict) -> tuple[User, bool]:
    """A DEMO_LOGIN user, made approved on first use."""
    user = db.exec(select(User).where(User.google_id == info["google_id"])).first()
    if user is not None:
        user.last_login = datetime.now(timezone.utc)
        db.add(user)
        db.commit()
        return user, False
    user = User(
        google_id=info["google_id"],
        email=info["email"],
        name=info["name"],
        status=UserStatus.APPROVED,
        approved_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, True


# =============================================================================
# ROUTER FACTORY FUNCTION
# =============================================================================


def create_snapgate_router(
    get_db: Callable,
    allowed_origins: list[str] | None = None,
    hooks: SnapgateHooks | None = None,
    api_key_prefix: str | None = None,
    scopes: "ScopeConfig | None" = None,
) -> APIRouter:
    """
    Factory function to create the snapgate auth router.

    Args:
        get_db: Database session dependency function that yields a SQLModel/SQLAlchemy Session
        allowed_origins: List of allowed frontend origins for OAuth redirects.
                        If None, any origin is allowed.
        hooks: Optional lifecycle hooks for events like login and user creation.
        api_key_prefix: Prefix on API keys this service issues and accepts,
                        e.g. "authormark_". Defaults to settings.API_KEY_PREFIX.
        scopes: The app's scope vocabulary (``ScopeConfig``). Settings-page keys
                get ``default_scopes`` unless the request names others. None:
                keys carry no scopes.

    Returns:
        Configured APIRouter with all authentication endpoints

    Example:
        ```python
        from sqlmodel import Session, create_engine
        from snapgate import SnapgateHooks

        engine = create_engine("sqlite:///./app.db")

        def get_db():
            with Session(engine) as session:
                yield session

        def on_login(user, db):
            logger.info(f"User {user.email} logged in")

        router = create_snapgate_router(
            get_db=get_db,
            hooks=SnapgateHooks(on_login=on_login)
        )
        app.include_router(router)
        ```
    """
    router = APIRouter(prefix="/auth", tags=["Authentication"])

    key_prefix = api_key_prefix or settings.API_KEY_PREFIX

    # Create auth dependencies with injected database dependency
    get_current_user, require_user, _ = create_auth_dependencies(get_db)

    # Create flexible auth dependencies for API key support
    from .dependencies import create_flexible_auth_dependencies

    get_current_user_flexible, _, _, _ = create_flexible_auth_dependencies(
        get_db, api_key_prefix=key_prefix
    )

    @router.get("/provider")
    async def sign_in_provider():
        """What the sign-in button should say, and which issuer it goes to."""
        return {"label": settings.OIDC_LABEL, "issuer": settings.OIDC_ISSUER}

    @router.get("/login")
    async def login(
        request: Request,
        return_to: str | None = Query(None, description="Frontend URL to redirect to after auth"),
        login_hint: str | None = Query(None, description="Account to pre-select at the issuer"),
        next: str | None = Query(
            None, description="OAuth consent page to return to (snapgate OAuth server only)"
        ),
        prompt: str | None = Query(None, description="Only select_account"),
    ):
        """Send the person to the OpenID Connect issuer to sign in.

        Args:
            return_to: Frontend URL to redirect to after successful auth.
                       Must be in ALLOWED_ORIGINS. Defaults to FRONTEND_URL.
            login_hint: Passed to the issuer (OIDC ``login_hint``), which may
                        pre-select that account.
            next: A same-site ``/oauth/authorize...`` path. When given, the
                  callback makes a short-lived consent-only sign-in instead of
                  SPA tokens and returns there.
            prompt: ``select_account`` to ask the issuer for its account picker.
        """
        enforce_rate_limit(request, "login", "login")
        if prompt is not None and prompt != "select_account":
            raise AuthError(user_message="Invalid prompt", error_code="invalid_prompt")
        extra_params = {}
        if login_hint:
            extra_params["login_hint"] = login_hint
        if prompt:
            extra_params["prompt"] = prompt
        if next is not None:
            request.session["sg_next"] = safe_consent_next(next)
            request.session.pop("oauth_redirect", None)
        else:
            # A plain SPA sign-in: forget any abandoned consent-page sign-in.
            request.session.pop("sg_next", None)
            frontend_redirect = return_to or settings.FRONTEND_URL
            parsed = urlsplit(frontend_redirect)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            # Fall back to settings.allowed_origins when the parameter is None
            origins_to_check = (
                allowed_origins if allowed_origins is not None else settings.allowed_origins
            )
            if origin not in origins_to_check:
                logger.warning(f"Rejected OAuth redirect to unauthorized origin: {origin}")
                raise AuthError(
                    user_message="Invalid redirect URL",
                    log_message=f"Redirect origin {origin} not in allowed_origins",
                    error_code="invalid_redirect",
                )
            # Stored in the session for the callback
            request.session["oauth_redirect"] = frontend_redirect
        try:
            client = await _oidc_client()
            return await client.authorize_redirect(
                request, settings.OIDC_REDIRECT_URI, **extra_params
            )
        except AuthError:
            raise
        except (OAuthError, AuthlibOAuthError, *_HTTP_ERRORS) as e:
            logger.error(f"OAuth initiation failed: {str(e)}")
            raise AuthError(
                user_message="Unable to reach the sign-in service. Please try again.",
                log_message=str(e),
                error_code="oauth_init_failed",
            ) from e

    @router.get("/login/callback")
    async def login_callback(
        request: Request, error: str | None = Query(None), db: Session = Depends(get_db)
    ):
        """The issuer sends the person back here with a code."""
        enforce_rate_limit(request, "login", "login_callback")
        # Get the frontend redirect URL from session (set during /login)
        frontend_url = request.session.pop("oauth_redirect", settings.FRONTEND_URL)
        consent_next = request.session.pop("sg_next", None)

        # Check for OAuth errors
        if error:
            logger.warning(f"OAuth error: {error}")
            return RedirectResponse(url=f"{frontend_url}/login?error=oauth_failed")

        try:
            # authlib checks state, then verifies the id_token: signature
            # against the issuer's JWKS, iss, aud, exp and nonce.
            client = await _oidc_client()
            token = await client.authorize_access_token(request)
            claims = token.get("userinfo")
            if not claims:
                raise OAuthError(settings.OIDC_ISSUER, "No id_token in the token response")

            logger.info(f"OAuth callback successful for {claims.get('email')}")
            try:
                user, is_new_user = await run_in_threadpool(sign_in_oidc_user, db, dict(claims))
            except AuthError as e:
                await _record_sign_in(
                    hooks,
                    db,
                    SignInAttempt(
                        email=claims.get("email") or "",
                        name=claims.get("name"),
                        user=None,
                        allowed=False,
                        reason=e.error_code,
                        ip=client_ip(request),
                        user_agent=_ua(request),
                        method="oidc",
                    ),
                )
                raise

            # SECURITY: Regenerate session ID to prevent session fixation attacks
            old_session = dict(request.session)
            request.session.clear()
            request.session.update(old_session)

            # Call lifecycle hooks (after transaction committed)
            # For new users, both hooks fire; for returning users, only on_login
            if is_new_user and hooks and hooks.on_user_created:
                await _call_hook(hooks.on_user_created, user, db, "on_user_created")
            if hooks and hooks.on_login:
                await _call_hook(hooks.on_login, user, db, "on_login")
            await _record_sign_in(hooks, db, _attempt_for(db, user, request, "oidc"))

            if consent_next:
                # Consent-page sign-in: no SPA tokens, just the short-lived
                # session marker snapgate.oauth reads, then back to the consent page.
                set_consent_user(request, user)
                return RedirectResponse(url=consent_next, status_code=302)

            # Generate single-use exchange code (tokens never in URL). Only its
            # sha256 is stored; the tokens are minted at /auth/exchange, so a
            # code that is never exchanged leaves no session behind. Snapgate
            # authenticates, apps authorize: apps check user.status from /auth/me.
            exchange_code = secrets.token_urlsafe(32)
            db.add(ExchangeCode(code_hash=hash_token(exchange_code), user_id=user.id))
            db.commit()

            # Redirect with just the code (not tokens)
            separator = "&" if "?" in frontend_url else "?"
            redirect_url = f"{frontend_url}{separator}code={exchange_code}"
            logger.info(f"OAuth redirect to {frontend_url} with exchange code")
            return RedirectResponse(url=redirect_url)

        except AuthError:
            raise  # Let exception handler deal with it
        except (AuthlibOAuthError, *_ID_TOKEN_ERRORS) as e:
            # This endpoint is public and unauthenticated: anyone can GET it with no
            # `state`/`code`, with a stale state, or with a replayed callback, and
            # authlib raises MismatchingStateError (or a sibling) from
            # authorize_access_token. Those are drive-by requests, not server faults,
            # so they get the same clean redirect as the other handled errors. Logged
            # at WARNING with a one-line message (never exc_info) so an anonymous
            # caller cannot fill the logs with tracebacks. An id_token that fails
            # verification (_ID_TOKEN_ERRORS) is handled the same way.
            logger.warning(f"OAuth callback rejected: {type(e).__name__}: {e}")
            return RedirectResponse(url=f"{frontend_url}/login?error=callback_failed")
        except (OAuthError, JWTError, *_HTTP_ERRORS) as e:
            logger.error(f"OAuth callback error: {str(e)}")
            return RedirectResponse(url=f"{frontend_url}/login?error=callback_failed")

    @router.post("/refresh")
    async def refresh_tokens(
        request: Request, body: RefreshTokenRequest, db: Session = Depends(get_db)
    ):
        """Refresh access token using refresh token with atomic rotation.

        Accepts refresh token in request body: {"refresh_token": "..."}
        Returns new access and refresh tokens in response body.
        """
        enforce_rate_limit(request, "refresh")
        try:
            service = AuthService(db)
            (
                user,
                new_access_token,
                new_refresh_token,
                access_expires,
            ) = await service.rotate_refresh_token(body.refresh_token, request)

            return {
                "message": "Tokens refreshed",
                "access_token": new_access_token,
                "refresh_token": new_refresh_token,
                "user": _user_json(user),
                "access_expires_at": access_expires.isoformat(),
            }

        except AuthError:
            raise
        except JWTError as e:
            logger.error(f"Refresh error: {str(e)}")
            raise AuthError(
                user_message="Failed to refresh session. Please sign in again.",
                log_message=str(e),
                error_code="refresh_failed",
            ) from e

    @router.post("/logout")
    async def logout(
        request: Request,
        body: LogoutRequest,
        db: Session = Depends(get_db),
        current_user: User | None = Depends(get_current_user_flexible),
    ):
        """Logout user and revoke tokens.

        Accepts refresh token in request body: {"refresh_token": "..."}
        Validates that the token belongs to the current user.
        """
        enforce_rate_limit(request, "logout")
        service = AuthService(db)
        user_id = current_user.id if current_user else None
        await service.logout_user(body.refresh_token, user_id)
        return {"message": "Logged out successfully"}

    @router.post("/exchange")
    async def exchange_code(
        request: Request,
        body: CodeExchangeRequest,
        db: Session = Depends(get_db),
    ):
        """
        Exchange a single-use code for tokens.

        This endpoint is called by the frontend after OAuth redirect.
        The code is valid for 30 seconds and can only be used once. The access
        and refresh tokens are minted here, not at the callback.
        """
        enforce_rate_limit(request, "exchange")
        # Lock the row to prevent concurrent use (TOCTOU race condition)
        statement = (
            select(ExchangeCode)
            .where(
                ExchangeCode.code_hash == hash_token(body.code),
                ExchangeCode.used.is_(False),
            )
            .with_for_update()
        )
        code_record = db.exec(statement).first()

        if not code_record:
            raise HTTPException(status_code=400, detail="Invalid or expired code")

        if code_record.is_expired:
            db.delete(code_record)
            db.commit()
            raise HTTPException(status_code=400, detail="Code expired")

        # Mark as used while still holding lock
        code_record.used = True
        db.add(code_record)

        user = db.get(User, code_record.user_id)
        if not user:
            db.rollback()
            raise HTTPException(status_code=400, detail="Invalid code")

        access_token, access_expires_at = create_access_token(user.id, user.email)
        _, refresh_plain = _add_refresh_token(
            db,
            user_id=user.id,
            client_ip=request.client.host,
            user_agent=request.headers.get("user-agent"),
        )

        # One commit writes the used code and the new refresh token together
        # and releases the lock.
        db.commit()

        return {
            "access_token": access_token,
            "refresh_token": refresh_plain,
            "user": _user_json(user, "picture"),
            "access_expires_at": access_expires_at.isoformat(),
        }

    @router.get("/me")
    async def get_current_user_info(user: User | None = Depends(get_current_user_flexible)):
        """
        Get current user information.

        Supports both OAuth (JWT Bearer token) and API key authentication.
        Returns user.status for apps to handle authorization/routing.
        """
        if not user:
            return {"user": None}

        # status: "pending", "approved", "denied", "suspended". google_id is the
        # stable identifier for tracking remembered accounts. site_role comes
        # from SNAPGATE_*_EMAILS ("superadmin", "admin" or null).
        return {"user": _user_json(user, "google_id", "picture", "created_at")}

    @router.get("/check")
    async def check_token_valid(
        authorization: str | None = Header(None),
    ):
        """Check if the current token is valid. Returns minimal info.

        This is a lightweight endpoint for checking token validity without
        doing a full user lookup. Useful for remembered accounts to check
        if a stored token is still valid before showing one-click login.
        """
        token = _parse_bearer_token(authorization)
        if not token:
            return {"valid": False}

        try:
            payload = verify_access_token(token)
            return {
                "valid": True,
                "user_id": payload.get("sub"),
                "email": payload.get("email"),
            }
        except (JWTError, InvalidTokenError, KeyError):
            return {"valid": False}

    # =============================================================================
    # API KEY MANAGEMENT ENDPOINTS
    # =============================================================================

    # Import API key functions
    from .api_keys import create_api_key, list_user_api_keys, revoke_api_key
    from .models import ApiKey

    def _key_scopes(requested: list[str] | None) -> list[str]:
        if scopes is None:
            if requested:
                raise AuthError(
                    user_message="This app does not use key scopes",
                    error_code="invalid_scope",
                )
            return []
        if requested is None:
            return sorted(scopes.expand(scopes.default_scopes))
        if not requested:
            raise AuthError(user_message="Choose at least one scope", error_code="invalid_scope")
        from .scopes import UnknownScopeError

        try:
            return sorted(scopes.validate(requested))
        except UnknownScopeError as e:
            raise AuthError(user_message=str(e), error_code="invalid_scope") from e

    @router.get("/scopes")
    async def list_scopes():
        """The app's public scope vocabulary (no auth)."""
        if scopes is None:
            return {"scopes": [], "groups": [], "default_scopes": []}
        return {
            "scopes": [{"name": n, "description": d} for n, d in scopes.scopes.items()],
            "groups": [
                {
                    "id": g.id,
                    "label": g.label,
                    "description": g.description,
                    "scopes": sorted(scopes.expand(g.scopes)),
                }
                for g in scopes.groups
            ],
            "default_scopes": sorted(scopes.expand(scopes.default_scopes)),
        }

    @router.post("/api-keys")
    async def create_user_api_key(
        body: CreateApiKeyRequest,
        user: User = Depends(require_user),  # Requires OAuth (JWT Bearer token)
        db: Session = Depends(get_db),
    ):
        """
        Create a new API key for the current user.

        This endpoint requires OAuth authentication (JWT Bearer token).
        Returns the API key - save it immediately as it won't be shown again.
        """
        key_scopes = _key_scopes(body.scopes)
        api_key_record, full_key = create_api_key(
            db,
            user.id,
            body.name,
            body.description,
            body.expires_days,
            key_prefix=key_prefix,
            scopes=key_scopes,
        )

        return {
            "message": "API key created successfully",
            "api_key": full_key,  # ONLY TIME THIS IS SHOWN
            "key_info": {
                "id": api_key_record.id,
                "name": api_key_record.name,
                "description": api_key_record.description,
                "scopes": list(api_key_record.scopes),
                "created_at": api_key_record.created_at.isoformat(),
                "expires_at": api_key_record.expires_at.isoformat()
                if api_key_record.expires_at
                else None,
            },
            "warning": "Save this key now - you won't be able to see it again!",
        }

    @router.get("/api-keys")
    async def list_api_keys(
        user: User = Depends(require_user),  # Requires OAuth
        db: Session = Depends(get_db),
    ):
        """List all API keys for the current user."""
        keys = list_user_api_keys(db, user.id)

        return {
            "api_keys": [
                {
                    "id": key.id,
                    "name": key.name,
                    "description": key.description,
                    "prefix": key.prefix,
                    "scopes": list(key.scopes),
                    "created_at": key.created_at.isoformat(),
                    "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
                    "expires_at": key.expires_at.isoformat() if key.expires_at else None,
                    "revoked": key.revoked,
                }
                for key in keys
            ]
        }

    @router.delete("/api-keys/{key_id}")
    async def revoke_user_api_key(
        key_id: int,
        user: User = Depends(require_user),  # Requires OAuth
        db: Session = Depends(get_db),
    ):
        """Revoke an API key."""
        # Verify key belongs to user
        key_record = db.get(ApiKey, key_id)
        # Connection keys are managed under /auth/connections.
        if not key_record or key_record.user_id != user.id or key_record.connection_id:
            raise AuthError(
                user_message="API key not found", status_code=404, error_code="api_key_not_found"
            )

        revoke_api_key(db, key_id)

        return {"message": "API key revoked successfully"}

    # Demo login endpoints (development only)
    if settings.DEMO_LOGIN:

        @router.post("/demo-login/{role}")
        async def demo_login(
            role: str,
            request: Request,
            db: Session = Depends(get_db),
            next: str | None = Query(None),
        ):
            """Demo login for development (DEMO_LOGIN=true only).

            Returns tokens in response body for frontend to store in localStorage.
            With ``next`` (an OAuth consent page path), makes the consent-only
            sign-in instead and redirects there (303).
            """
            if next is not None:
                next = safe_consent_next(next)
            demo_users = {
                "admin": {
                    "google_id": "demo_admin_123",
                    "email": "admin@demo.local",
                    "name": "Demo Admin",
                    "sub": "demo_admin_123",
                },
                "user": {
                    "google_id": "demo_user_456",
                    "email": "user@demo.local",
                    "name": "Demo User",
                    "sub": "demo_user_456",
                },
            }

            if role not in demo_users:
                raise HTTPException(400, "Invalid demo role")

            user, is_new_user = _demo_user(db, demo_users[role])
            service = AuthService(db)

            # SECURITY: Regenerate session ID to prevent session fixation attacks
            old_session = dict(request.session)
            request.session.clear()
            request.session.update(old_session)

            # Demo users are always approved
            if user.status != UserStatus.APPROVED:
                user.status = UserStatus.APPROVED
                db.add(user)
                db.commit()

            # Call lifecycle hooks (after transaction committed)
            if is_new_user and hooks and hooks.on_user_created:
                await _call_hook(hooks.on_user_created, user, db, "on_user_created")
            if hooks and hooks.on_login:
                await _call_hook(hooks.on_login, user, db, "on_login")
            await _record_sign_in(hooks, db, _attempt_for(db, user, request, "demo"))

            if next is not None:
                # Consent-page sign-in: no SPA tokens.
                set_consent_user(request, user)
                return RedirectResponse(url=next, status_code=303)

            access_token, access_expires = create_access_token(user.id, user.email)
            _, refresh_plain = await service.create_refresh_token(user.id, request)

            return {
                "message": f"Demo login as {role}",
                "access_token": access_token,
                "refresh_token": refresh_plain,
                "user": _user_json(user, "picture"),
                "access_expires_at": access_expires.isoformat(),
            }

    return router
