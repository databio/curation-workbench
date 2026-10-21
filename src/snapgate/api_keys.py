# SNAPGATE module - do not edit. Overwritten on updates. Customize via SnapgateHooks.
"""API Key management for Snapgate."""

import hashlib
import logging
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy import delete, func, update
from sqlmodel import Session, col, select

from .config import settings
from .models import ApiKey, User, UserStatus

logger = logging.getLogger(__name__)

# Default prefix for this deployment. Configure via the API_KEY_PREFIX env var,
# or pass an explicit prefix to create_snapgate_router()/the functions below.
API_KEY_PREFIX = settings.API_KEY_PREFIX
API_KEY_BYTES = 32  # 43 characters when base64url encoded


API_KEY_PREFIX_LENGTH = 16  # Characters to store for efficient lookup


@dataclass(frozen=True)
class MatchedKey:
    """Safe summary of the API key that authenticated a request.

    Deliberately excludes key_hash and prefix so apps cannot leak them
    into responses or logs. Load the ApiKey row by id if more is needed.
    """

    id: int
    name: str
    scopes: tuple[str, ...] = ()
    # Set on keys an OAuth connection minted: the path they work under.
    confined_to: str | None = None
    connection_id: int | None = None


@dataclass(frozen=True)
class ApiKeyMatch:
    """A verified API key and the approved user it belongs to."""

    user: User
    key: MatchedKey


def generate_api_key(prefix: str | None = None) -> tuple[str, str]:
    """
    Generate a new API key.

    Args:
        prefix: Key prefix, e.g. "authormark_". Defaults to settings.API_KEY_PREFIX.

    Returns:
        Tuple of (full_key, stored_prefix)
        full_key: Complete key to give to user (only time they'll see it)
        stored_prefix: First 16 chars of full key for efficient database lookup
    """
    prefix = prefix or settings.API_KEY_PREFIX
    random_part = secrets.token_urlsafe(API_KEY_BYTES)
    full_key = f"{prefix}{random_part}"
    stored_prefix = full_key[:API_KEY_PREFIX_LENGTH]  # Store first 16 chars for lookup
    return full_key, stored_prefix


def hash_api_key(key: str) -> str:
    """Hash an API key using bcrypt."""
    return bcrypt.hashpw(key.encode(), bcrypt.gensalt()).decode()


def verify_api_key(key: str, key_hash: str) -> bool:
    """Verify an API key against its hash."""
    try:
        return bcrypt.checkpw(key.encode(), key_hash.encode())
    except (ValueError, TypeError) as e:
        logger.warning(f"API key verification failed: {str(e)}")
        return False


def create_api_key(
    db: Session,
    user_id: int,
    name: str,
    description: str | None = None,
    expires_days: int | None = None,
    key_prefix: str | None = None,
    *,
    scopes: Sequence[str] = (),
    confined_to: str | None = None,
    connection_id: int | None = None,
    expires_at: datetime | None = None,
    commit: bool = True,
) -> tuple[ApiKey, str]:
    """
    Create a new API key for a user.

    Args:
        db: Database session
        user_id: User ID to create key for
        name: Human-readable name
        description: Optional description
        expires_days: Optional expiration in days
        key_prefix: Key prefix, e.g. "authormark_". Defaults to settings.API_KEY_PREFIX.
        scopes: Scope names to store (already validated and expanded by the caller).
        confined_to: Path the key works under (OAuth connection keys only).
        connection_id: The OAuth connection that minted the key.
        expires_at: Exact expiry; wins over ``expires_days``.
        commit: False flushes instead, so the caller can commit this key together
                with other rows in one transaction.

    Returns:
        Tuple of (ApiKey record, full_key_string)
        WARNING: full_key_string is the ONLY time the unhashed key is available
    """
    # Generate key
    full_key, prefix = generate_api_key(key_prefix)
    key_hash = hash_api_key(full_key)

    # Calculate expiration
    if expires_at is None and expires_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)

    # Create record
    api_key = ApiKey(
        user_id=user_id,
        name=name,
        description=description,
        key_hash=key_hash,
        prefix=prefix,
        expires_at=expires_at,
        scopes=list(scopes),
        confined_to=confined_to,
        connection_id=connection_id,
    )

    db.add(api_key)
    if commit:
        db.commit()
        db.refresh(api_key)
    else:
        db.flush()

    logger.info(f"API key '{name}' created for user {user_id}")

    return api_key, full_key


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


#: Monotonic clock for the verified-token cache (patched in tests).
_monotonic = time.monotonic

# Verified-token cache: sha256(token) -> (key id, its bcrypt hash, expiry on
# _monotonic). It only remembers "this token matched key N (whose hash was H)",
# so repeat requests skip bcrypt (about 200 ms each). Whether key N is still
# valid is decided by a primary-key read on every request, never by the cache.
_verified: "OrderedDict[str, tuple[int, str, float]]" = OrderedDict()
_verified_lock = threading.Lock()


def clear_verified_cache() -> None:
    """Forget every cached token match (tests; key rotation tooling)."""
    with _verified_lock:
        _verified.clear()


def _cache_get(digest: str) -> tuple[int, str] | None:
    with _verified_lock:
        hit = _verified.get(digest)
        if hit is None:
            return None
        if hit[2] <= _monotonic():
            _verified.pop(digest, None)
            return None
        _verified.move_to_end(digest)
        return hit[0], hit[1]


def _cache_put(digest: str, key_id: int, key_hash: str) -> None:
    size = max(0, settings.API_KEY_VERIFY_CACHE_SIZE)
    if size == 0 or settings.API_KEY_VERIFY_CACHE_S <= 0:
        return
    with _verified_lock:
        _verified[digest] = (key_id, key_hash, _monotonic() + settings.API_KEY_VERIFY_CACHE_S)
        _verified.move_to_end(digest)
        while len(_verified) > size:
            _verified.popitem(last=False)


def _cache_evict(digest: str) -> None:
    with _verified_lock:
        _verified.pop(digest, None)


def _load(db: Session, key_id: int) -> tuple[ApiKey, User] | None:
    """One primary-key read: the key row and its user."""
    return db.exec(
        select(ApiKey, User).join(User, User.id == ApiKey.user_id).where(ApiKey.id == key_id)
    ).first()


def _still_valid(key_record: ApiKey, user: User) -> bool:
    """Not revoked, not expired, and the user is APPROVED."""
    if key_record.revoked:
        return False
    if key_record.expires_at:
        expires = key_record.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires:
            return False
    return user.status == UserStatus.APPROVED


def _touch_last_used(db: Session, key_id: int) -> None:
    """Write ``last_used_at`` at most once per resolution window, committing
    only when the row was written.

    One conditional UPDATE, so two workers never race and a burst on one key
    writes the row about once a minute instead of on every request.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=max(0, settings.API_KEY_LAST_USED_RESOLUTION_S))
    result = db.exec(
        update(ApiKey)
        .where(
            ApiKey.id == key_id,
            (col(ApiKey.last_used_at).is_(None)) | (col(ApiKey.last_used_at) < cutoff),
        )
        .values(last_used_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount:
        db.commit()


def authenticate_api_key(
    db: Session, api_key: str, key_prefix: str | None = None
) -> ApiKeyMatch | None:
    """
    Verify an API key and return the associated user and which key matched.

    Repeat requests with the same token skip bcrypt: a bounded LRU
    (``API_KEY_VERIFY_CACHE_SIZE`` entries, ``API_KEY_VERIFY_CACHE_S`` TTL)
    maps sha256(token) to the key id it matched. On every call, hit or miss,
    the key row and its user are re-read by primary key and checked (revoked,
    expired, user not APPROVED), so a revoked key is refused on the next
    request. bcrypt never runs inside a transaction: the candidate read is
    committed first, which returns the connection to the pool. ``db.commit()``
    is used (not rollback), so a caller's pending work is committed, as before.

    Args:
        db: Database session
        api_key: Full API key string
        key_prefix: Expected key prefix. Defaults to settings.API_KEY_PREFIX.

    Returns:
        ApiKeyMatch (user + MatchedKey) if key is valid, None otherwise
    """
    # Check format
    if not api_key.startswith(key_prefix or settings.API_KEY_PREFIX):
        return None

    digest = _digest(api_key)
    hit = _cache_get(digest)
    row = None
    if hit is not None:
        row = _load(db, hit[0])
        # The id must still name the key the token matched (same stored hash,
        # a plain string compare): a rebuilt database can reuse ids. If not,
        # forget it and verify from scratch.
        if row is None or row[0].key_hash != hit[1]:
            _cache_evict(digest)
            hit = row = None

    if hit is None:
        prefix = api_key[:API_KEY_PREFIX_LENGTH]
        candidates = [
            (cid, key_hash)
            for cid, key_hash in db.exec(
                select(ApiKey.id, ApiKey.key_hash).where(
                    ApiKey.prefix == prefix, ApiKey.revoked.is_(False)
                )
            ).all()
        ]
        # End the read transaction before hashing: the connection goes back
        # to the pool while bcrypt runs (~200 ms).
        db.commit()
        for cid, key_hash in candidates:
            if verify_api_key(api_key, key_hash):
                hit = (cid, key_hash)
                break
        if hit is None:
            return None
        _cache_put(digest, *hit)
        row = _load(db, hit[0])

    if row is None or not _still_valid(*row):
        _cache_evict(digest)
        db.commit()
        return None
    key_record, user = row
    match = ApiKeyMatch(
        user=user,
        key=MatchedKey(
            id=key_record.id,
            name=key_record.name,
            scopes=tuple(key_record.scopes or ()),
            confined_to=key_record.confined_to,
            connection_id=key_record.connection_id,
        ),
    )
    _touch_last_used(db, key_record.id)
    logger.debug("API key id=%s authenticated user %s", key_record.id, user.id)
    return match


def revoke_api_key(db: Session, key_id: int) -> bool:
    """
    Revoke an API key.

    Returns:
        True if revoked, False if not found
    """
    key_record = db.get(ApiKey, key_id)
    if not key_record:
        return False

    key_record.revoked = True
    db.add(key_record)
    db.commit()

    logger.info(f"API key '{key_record.name}' revoked for user {key_record.user_id}")
    return True


def list_user_api_keys(db: Session, user_id: int) -> list[ApiKey]:
    """List a user's Settings-page API keys (active and revoked).

    Keys minted by OAuth connections are left out: they are managed as
    connections under ``/auth/connections``.
    """
    statement = select(ApiKey).where(ApiKey.user_id == user_id, col(ApiKey.connection_id).is_(None))
    return db.exec(statement).all()


def revoke_connection_keys(db: Session, connection_id: int, commit: bool = True) -> int:
    """Revoke every key an OAuth connection minted. Returns how many changed."""
    result = db.exec(
        update(ApiKey)
        .where(col(ApiKey.connection_id) == connection_id, col(ApiKey.revoked).is_(False))
        .values(revoked=True),
        execution_options={"synchronize_session": False},
    )
    if commit:
        db.commit()
    return int(result.rowcount or 0)


def set_connection_key_scopes(
    db: Session, connection_id: int, scopes: Sequence[str], commit: bool = True
) -> None:
    """Rewrite ``scopes`` on a connection's unrevoked keys, so a change applies at once."""
    keys = db.exec(
        select(ApiKey).where(ApiKey.connection_id == connection_id, col(ApiKey.revoked).is_(False))
    ).all()
    for key in keys:
        key.scopes = list(scopes)
        db.add(key)
    if commit:
        db.commit()
    else:
        db.flush()


def prune_connection_keys(db: Session, connection_id: int, before: datetime) -> int:
    """Delete a connection's keys that expired before ``before``, never its newest.

    A connection mints a key every hour; without this the table gains a row per
    hour per connection forever.
    """
    newest = db.exec(
        select(func.max(ApiKey.id)).where(ApiKey.connection_id == connection_id)
    ).first()
    result = db.exec(
        delete(ApiKey).where(
            col(ApiKey.connection_id) == connection_id,
            col(ApiKey.expires_at).is_not(None),
            col(ApiKey.expires_at) < before,
            col(ApiKey.id) != newest,
        ),
        execution_options={"synchronize_session": False},
    )
    db.commit()
    return int(result.rowcount or 0)
