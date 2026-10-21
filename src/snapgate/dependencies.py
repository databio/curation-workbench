# SNAPGATE module - do not edit. Overwritten on updates. Customize via SnapgateHooks.
"""
Flexible authentication dependencies that support both JWT and API key auth.

This module provides factory functions to create auth dependencies that can
authenticate via OAuth-issued JWT tokens OR API keys (for programmatic access).
"""

from collections.abc import Callable

from fastapi import Depends, Header, Request
from sqlmodel import Session

from .api_keys import MatchedKey, authenticate_api_key
from .core import (
    InvalidTokenError,
    UserNotApprovedError,
    _parse_bearer_token,
    create_auth_dependencies,
)
from .models import User, UserStatus
from .scopes import enforce_confinement


def create_flexible_auth_dependencies(
    get_db: Callable,
    api_key_prefix: str | None = None,
) -> tuple[Callable, Callable, Callable, Callable]:
    """
    Create flexible auth dependencies that support both JWT and API key auth.

    Args:
        get_db: Database session dependency function
        api_key_prefix: Expected prefix on API keys, e.g. "authormark_".
                        Defaults to settings.API_KEY_PREFIX.

    Returns:
        Tuple of (get_current_user_flexible, require_user_flexible,
        require_approved_user_flexible, get_request_api_key).
        ``require_approved_user_flexible`` is the one you almost always want
        for owner/authorization gates — the plain ``require_user_flexible`` is
        IDENTITY only and admits PENDING accounts. ``get_request_api_key``
        reports which API key (id and name only) authenticated the request,
        or None for JWT/anonymous callers.

        Scopes: build ``require_scope`` with
        ``create_scope_guard(scopes, get_request_api_key)``, passing this same
        ``get_request_api_key`` callable that the user dependencies use, so
        FastAPI's per-request cache verifies the key once.

        Confinement: a key minted by an OAuth connection (``confined_to`` set)
        raises ``KeyConfinedError`` (403) outside its path. That is a valid key
        in the wrong place, not a bad credential.
    """
    # Get the standard auth dependencies
    get_current_user, require_user, _ = create_auth_dependencies(get_db)

    def get_user_from_api_key(
        request: Request,
        authorization: str | None = Header(None),
        db: Session = Depends(get_db),
    ) -> User | None:
        """Get user from API key in Authorization header.

        Sets ``request.state.api_key`` to the MatchedKey on success, else None.
        Raises ``KeyConfinedError`` for a confined key outside its path.
        """
        request.state.api_key = None
        api_key = _parse_bearer_token(authorization)
        if not api_key:
            return None

        match = authenticate_api_key(db, api_key, key_prefix=api_key_prefix)
        if match is None:
            return None
        enforce_confinement(request, match.key)
        request.state.api_key = match.key
        return match.user

    def get_current_user_flexible(
        user_from_token: User | None = Depends(get_current_user),
        user_from_api_key: User | None = Depends(get_user_from_api_key),
    ) -> User | None:
        """Get user from either JWT token (OAuth) or API key (programmatic)."""
        return user_from_token or user_from_api_key

    def require_user_flexible(user: User | None = Depends(get_current_user_flexible)) -> User:
        """Require an authenticated user via any method (JWT token or API key).

        IDENTITY ONLY — resolves a user of ANY status, including PENDING. Do NOT
        use this as an authorization gate; a just-signed-in account is PENDING
        and would pass. Use ``require_approved_user_flexible`` for owner/action
        routes. (Mirrors the warning on ``require_user`` in core.py.)
        """
        if not user:
            raise InvalidTokenError("Authentication required")
        return user

    def require_approved_user_flexible(
        user: User = Depends(require_user_flexible),
    ) -> User:
        """Require an authenticated user (JWT or API key) whose status is APPROVED.

        The flexible analogue of ``require_approved_user``: the authorization
        gate apps should use for owner/action routes. A PENDING/DENIED/SUSPENDED
        account raises ``UserNotApprovedError`` (403). The API-key path is
        already APPROVED-only (``authenticate_api_key``); this closes the JWT path
        too, in one place, so consumers don't reinvent the check.
        """
        if user.status != UserStatus.APPROVED:
            raise UserNotApprovedError(user.email)
        return user

    def get_request_api_key(
        request: Request,
        _user: User | None = Depends(get_current_user_flexible),
    ) -> MatchedKey | None:
        """The API key that authenticated this request, or None (JWT / anonymous)."""
        return getattr(request.state, "api_key", None)

    return (
        get_current_user_flexible,
        require_user_flexible,
        require_approved_user_flexible,
        get_request_api_key,
    )
