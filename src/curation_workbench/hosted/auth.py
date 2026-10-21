"""Snapgate wiring: the auth router, invite-only hooks, and auth dependencies.

Modeled on Pigeon's `pigeon/auth.py`, minus link callers. Every route that
needs a signed-in user uses :data:`CurrentCaller`: a JWT (browser session) or
a ``cwb_`` API key, APPROVED accounts only (else 403 ``user_not_approved``),
with failed-credential counting and the per-user request limit, as a
:class:`snapgate.Caller`. It checks no scope: every route calls a
:mod:`curation_workbench.hosted.service` function, and the function checks.

**Invite-only.** Nobody gets in by default. Snapgate approves the very first
account ever created; :func:`on_user_created` sets it back to PENDING unless
the email is env-listed (``SNAPGATE_SUPERADMIN_EMAILS`` /
``SNAPGATE_ADMIN_EMAILS``; snapgate approves those at every sign-in anyway).
A superadmin approves accounts through snapgate's user admin
(``/auth/admin/users/{id}/approve``), then adds them to a project's team
(``PUT /api/v1/admin/projects/{slug}/team/{user_id}``). No app roles are
registered with snapgate: its ``RoleSpec`` is global, and team membership is
per project (``project_members``). The hooks are wrapped by
``UserAdmin.snapgate_hooks``, so every sign-in attempt is logged (a pending
account as ``allowed=False``).
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from sqlmodel import Session

from snapgate import Caller, MatchedKey, SnapgateHooks, User, UserStatus, caller_for, create_snapgate_router
from snapgate import useradmin
from snapgate.core import InvalidTokenError, UserNotApprovedError
from snapgate.dependencies import create_flexible_auth_dependencies
from snapgate.useradmin import UserAdmin, UserAdminHooks, create_useradmin_router

from . import ratelimit
from .config import Settings
from .db import get_session
from .scopes import SCOPES

logger = logging.getLogger("curation_workbench.hosted.auth")

#: Prefix on every API key the hosted service issues and accepts.
API_KEY_PREFIX = "cwb_"


# -- hooks ---------------------------------------------------------------------


def on_user_created(user: User, db: Session) -> None:
    """Invite-only: a new account starts PENDING, whatever snapgate decided,
    unless its email holds a site role."""
    row = db.get(User, user.id)
    if row is None:
        return
    if row.status == UserStatus.APPROVED and useradmin.site_role(row) is None:
        row.status = UserStatus.PENDING
        row.approved_at = None
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("new account %s is pending approval", row.email)


def on_login(user: User, db: Session) -> None:
    """Nothing: approval is a superadmin's act."""


def build_user_admin(settings: Settings | None = None) -> UserAdmin:
    """Snapgate's user admin with no app roles (approve, suspend, the user
    list and the sign-in log only)."""
    return UserAdmin(UserAdminHooks(roles={}))


def snapgate_hooks(admin: UserAdmin) -> SnapgateHooks:
    """The invite-only hooks, plus snapgate's sign-in log (runs after them)."""
    return admin.snapgate_hooks(SnapgateHooks(on_user_created=on_user_created, on_login=on_login))


def build_auth_router(admin: UserAdmin, get_db=get_session) -> APIRouter:
    """Snapgate's router (`/auth/...`): Prosopia sign-in (`/auth/login`,
    `/auth/login/callback`), `/auth/exchange`, `/auth/me`, `/auth/refresh`,
    `/auth/logout`, `/auth/api-keys`, `/auth/scopes`, and
    `/auth/demo-login/{role}` when DEMO_LOGIN=true."""
    return create_snapgate_router(get_db=get_db, hooks=snapgate_hooks(admin),
                                  api_key_prefix=API_KEY_PREFIX, scopes=SCOPES)


# -- dependencies --------------------------------------------------------------

(
    get_current_user_flexible,
    require_user_flexible,
    require_approved_user_flexible,
    get_request_api_key,
) = create_flexible_auth_dependencies(get_session, api_key_prefix=API_KEY_PREFIX)


def _guard_credentials(request: Request, authorization: str | None = Header(None)) -> None:
    """Runs BEFORE credential resolution: 429 an IP with too many recent failures."""
    if authorization:
        ratelimit.guard_credential_attempt(request)


def _authenticated(request: Request, authorization: str | None, user: User | None,
                   key: MatchedKey | None) -> tuple[User, MatchedKey | None]:
    """An APPROVED user by JWT or key; counts failed credentials; per-user request limit."""
    if user is None:
        if authorization:
            ratelimit.record_bad_credential(request)
        raise InvalidTokenError("Authentication required")
    if user.status != UserStatus.APPROVED:
        raise UserNotApprovedError(user.email)
    if not getattr(request.state, "_user_request_counted", False):
        request.state._user_request_counted = True
        ratelimit.enforce(request, "user_requests", str(user.id))
    return user, key


def require_caller(
    request: Request,
    _guard: None = Depends(_guard_credentials),
    authorization: str | None = Header(None),
    user: User | None = Depends(get_current_user_flexible),
    key: MatchedKey | None = Depends(get_request_api_key),
) -> Caller:
    """Who is asking, as a `snapgate.Caller`. NO scope check here: the service
    function the route calls checks. A browser session holds every scope; a
    key holds its own, expanded."""
    user, key = _authenticated(request, authorization, user, key)
    return caller_for(user, key, SCOPES)


def build_useradmin_router(admin: UserAdmin, get_db=get_session) -> APIRouter:
    """`/auth/admin/users[?status=...]`, `/auth/admin/users/{id}/approve|suspend|unsuspend`,
    `/auth/admin/logins` (site admins, browser session only), with our own caller."""
    return create_useradmin_router(get_db, admin, get_caller=require_caller)


CurrentCaller = Annotated[Caller, Depends(require_caller)]


__all__ = [
    "API_KEY_PREFIX",
    "CurrentCaller",
    "build_auth_router",
    "build_user_admin",
    "build_useradmin_router",
    "get_current_user_flexible",
    "get_request_api_key",
    "on_login",
    "on_user_created",
    "require_caller",
    "snapgate_hooks",
]
