# SNAPGATE module - do not edit. Overwritten on updates. Customize via UserAdminHooks.
"""User admin for snapgate apps (optional): site admins, a sign-in log, account
approve/suspend, and requests for app-defined roles.

Importing this module registers its three tables (``snapgate_login_events``,
``snapgate_account_audit``, ``snapgate_role_requests``) with SQLModel, so
``create_all`` makes them. An app that migrates by hand copies
``snapgate/sql/useradmin.sql`` into its own numbered migration.

Site roles come only from ``SNAPGATE_SUPERADMIN_EMAILS`` / ``SNAPGATE_ADMIN_EMAILS``
(read on every call). Env-listed people are approved at every sign-in and
cannot be suspended or approved through the API. Admin powers need a browser
session unless ``UserAdminHooks.admin_via`` says otherwise.

Wiring::

    from snapgate import create_snapgate_router
    from snapgate.useradmin import RoleSpec, UserAdmin, UserAdminHooks, create_useradmin_router

    admin = UserAdmin(UserAdminHooks(roles={"writer": RoleSpec(label="Writer", holds=..., on_approve=...)}))
    app.include_router(create_snapgate_router(get_db=get_db, hooks=admin.snapgate_hooks(my_hooks)))
    app.include_router(create_useradmin_router(get_db, admin))

What suspension revokes: the user's refresh tokens and unused exchange codes.
API keys and OAuth connections are kept but stop working at once, because
``authenticate_api_key`` and the OAuth token endpoints refuse non-APPROVED
users; they work again after unsuspend. A live access JWT (at most
ACCESS_TOKEN_EXPIRE_MINUTES) still passes identity-only routes, but every
``require_approved_user*`` route re-reads the user and refuses it.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, CheckConstraint, Column, DateTime, Index, func, or_, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel, col, select

from .config import SiteRole, settings
from .core import SignInAttempt, SnapgateHooks, atomic_transaction, enforce_rate_limit
from .models import ExchangeCode, RefreshToken, User, UserStatus, _utcnow
from .scopes import ScopeConfig
from .service import (
    Caller,
    Conflict,
    Forbidden,
    Invalid,
    NotFound,
    ServiceError,
    caller_for,
    http_headers,
)

JSONB = JSON().with_variant(postgresql.JSONB(), "postgresql")

AUDIT_ACTIONS = (
    "approve",
    "suspend",
    "unsuspend",
    "deny",
    "role_request_approve",
    "role_request_reject",
    "role_request_withdraw",
    "role_revoke",
)
REQUEST_STATUSES = ("pending", "approved", "rejected", "withdrawn")

ROLE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
MAX_REASON = 500
MAX_MESSAGE = 2000
MAX_NOTE = 1000
MAX_DETAILS_BYTES = 16 * 1024


# =============================================================================
# TABLES
# =============================================================================


class LoginEvent(SQLModel, table=True):
    """One sign-in attempt that reached an identity, allowed or not."""

    __tablename__ = "snapgate_login_events"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int | None = Field(default=None, foreign_key="snapgate_users.id", index=True)
    email: str = Field(index=True)
    name: str | None = None
    allowed: bool
    reason: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    method: str = "oidc"  # "oidc" | "demo"
    at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )


class AccountAudit(SQLModel, table=True):
    """Append-only. user_id / actor_user_id are NOT foreign keys: the record outlives rows."""

    __tablename__ = "snapgate_account_audit"
    __table_args__ = (
        CheckConstraint(
            "action IN ('approve', 'suspend', 'unsuspend', 'deny', 'role_request_approve', "
            "'role_request_reject', 'role_request_withdraw', 'role_revoke')",
            name="ck_snapgate_account_audit_action",
        ),
        Index("ix_snapgate_account_audit_user_at", "user_id", "at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    actor_user_id: int | None = None
    actor_label: str
    action: str
    details: dict = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}")
    )
    at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )


class RoleRequest(SQLModel, table=True):
    """A person's request for an app-defined role."""

    __tablename__ = "snapgate_role_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'withdrawn')",
            name="ck_snapgate_role_requests_status",
        ),
        Index(
            "uq_snapgate_role_requests_pending",
            "user_id",
            "role",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="snapgate_users.id", index=True)
    role: str = Field(index=True)
    status: str = Field(default="pending", index=True)
    message: str = ""  # plain text, at most MAX_MESSAGE chars
    details: dict = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}")
    )
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    decided_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    decided_by: int | None = None  # NOT a foreign key
    decision_note: str | None = None


# =============================================================================
# HOOKS
# =============================================================================


def _keep(db: Session, user: User, details: dict) -> dict:
    return details


@dataclass(frozen=True)
class RoleSpec:
    """An app role people may request.

    ``holds(db, user)``: does the user already hold it (submit refuses if so).
    ``on_approve(db, request)``: grant it. Runs inside the approval's
    transaction and MUST NOT commit; raising aborts the whole approval.
    ``validate(db, user, details)``: return cleaned details or raise ``Invalid``.
    It runs at submit and again at approve.
    """

    label: str
    holds: Callable[[Session, User], bool]
    on_approve: Callable[[Session, RoleRequest], None]
    validate: Callable[[Session, User, dict], dict] = _keep
    description: str | None = None
    open_to_pending: bool = False  # may PENDING accounts apply?


@dataclass
class UserAdminHooks:
    roles: dict[str, RoleSpec] = field(default_factory=dict)
    # Credentials that may use admin powers. Default: browser sessions only.
    admin_via: frozenset[str] = frozenset({"session"})


# =============================================================================
# ERRORS
# =============================================================================


class NotAdmin(Forbidden):
    code = "not_admin"


class SessionRequired(Forbidden):
    code = "session_required"


class AccountNotEligible(Forbidden):
    code = "account_not_eligible"


class EnvAdmin(Conflict):
    code = "env_admin"


class SelfAction(Conflict):
    code = "self_action"


class AlreadySuspended(Conflict):
    code = "already_suspended"


class NotSuspended(Conflict):
    code = "not_suspended"


class UnknownRole(NotFound):
    code = "unknown_role"


class AlreadyHolds(Conflict):
    code = "already_holds"


class RequestPending(Conflict):
    code = "request_pending"


class NotPending(Conflict):
    code = "not_pending"


class NotYourRequest(NotFound):
    code = "not_your_request"


# =============================================================================
# OUTPUT MODELS
# =============================================================================


def _utc(dt: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; they are UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class UserRow(BaseModel):
    id: int
    email: str
    name: str
    picture: str | None
    status: str
    site_role: SiteRole | None
    created_at: datetime | None
    last_login: datetime | None
    approved_at: datetime | None
    login_count: int
    last_sign_in_at: datetime | None
    pending_requests: list[str]
    status_reason: str | None
    status_changed_by: int | None
    status_changed_at: datetime | None


class UserPage(BaseModel):
    users: list[UserRow]
    total: int


class LoginEventOut(BaseModel):
    id: int
    user_id: int | None
    email: str
    name: str | None
    allowed: bool
    reason: str | None
    ip: str | None
    user_agent: str | None
    method: str
    at: datetime

    @classmethod
    def of(cls, e: LoginEvent) -> LoginEventOut:
        return cls(**{**e.model_dump(), "at": _utc(e.at)})


class AuditOut(BaseModel):
    id: int
    user_id: int
    actor_user_id: int | None
    actor_label: str
    action: str
    details: dict
    at: datetime

    @classmethod
    def of(cls, a: AccountAudit) -> AuditOut:
        return cls(**{**a.model_dump(), "at": _utc(a.at)})


class RoleRequestOut(BaseModel):
    id: int
    user_id: int
    role: str
    role_label: str
    user_email: str
    user_name: str
    status: str
    message: str
    details: dict
    created_at: datetime
    decided_at: datetime | None
    decided_by: int | None
    decision_note: str | None


class UserDetail(BaseModel):
    user: UserRow
    logins: list[LoginEventOut]
    audit: list[AuditOut]
    role_requests: list[RoleRequestOut]


class RoleName(BaseModel):
    name: str
    label: str


class RolesOut(BaseModel):
    site_role: SiteRole | None
    roles: list[RoleName]
    held: list[str]
    pending: list[RoleRequestOut]


class UserEnvelope(BaseModel):
    user: UserRow


class LoginList(BaseModel):
    logins: list[LoginEventOut]


class RequestList(BaseModel):
    requests: list[RoleRequestOut]


class RequestEnvelope(BaseModel):
    request: RoleRequestOut


# =============================================================================
# FREE FUNCTIONS
# =============================================================================


def site_role(user: User) -> SiteRole | None:
    """``superadmin`` > ``admin`` > None, from the env lists."""
    return settings.site_role_for(user.email)


def actor_label(caller: Caller) -> str:
    email = caller.user.email
    return email if caller.via == "session" else f"{email} ({caller.via})"


def require_site_admin(caller: Caller, admin_via: frozenset[str] = frozenset({"session"})) -> None:
    """Raise unless ``caller`` is an APPROVED env-listed admin on an allowed credential."""
    if caller.via not in admin_via:
        raise SessionRequired("Admin actions need a browser session.")
    if site_role(caller.user) is None or caller.user.status != UserStatus.APPROVED:
        raise NotAdmin("Only site admins can do that.")


def record_sign_in(db: Session, attempt: SignInAttempt) -> None:
    """The ``on_sign_in_attempt`` hook: one LoginEvent row. Core commits."""
    db.add(
        LoginEvent(
            user_id=attempt.user.id if attempt.user is not None else None,
            email=attempt.email,
            name=attempt.name,
            allowed=attempt.allowed,
            reason=attempt.reason,
            ip=attempt.ip,
            user_agent=attempt.user_agent,
            method=attempt.method,
        )
    )


def _audit(db: Session, caller: Caller, user_id: int, action: str, details: dict) -> None:
    db.add(
        AccountAudit(
            user_id=user_id,
            actor_user_id=caller.user.id,
            actor_label=actor_label(caller),
            action=action,
            details=details,
        )
    )


def _like(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# =============================================================================
# SERVICE
# =============================================================================


class UserAdmin:
    """The user-admin service. Every method takes ``db, caller`` and raises typed errors."""

    def __init__(self, hooks: UserAdminHooks | None = None):
        self.hooks = hooks or UserAdminHooks()
        bad = [name for name in self.hooks.roles if not ROLE_NAME.fullmatch(name)]
        if bad:
            raise ValueError(f"role names must match {ROLE_NAME.pattern}: {bad}")

    def snapgate_hooks(self, base: SnapgateHooks | None = None) -> SnapgateHooks:
        """``base`` (or empty hooks) with the sign-in log added."""
        if base is not None and base.on_sign_in_attempt is not None:
            raise ValueError("base hooks already set on_sign_in_attempt")
        return dataclasses.replace(base or SnapgateHooks(), on_sign_in_attempt=record_sign_in)

    def _admin(self, caller: Caller) -> None:
        require_site_admin(caller, self.hooks.admin_via)

    def _label(self, role: str) -> str:
        spec = self.hooks.roles.get(role)
        return spec.label if spec else role

    # -- user rows --------------------------------------------------------------

    def _rows(self, db: Session, found: list[tuple[User, datetime | None, int | None]]):
        """UserRows for ``(user, last_sign_in_at, login_count)``: two queries, any page size."""
        ids = [u.id for u, _, _ in found]
        pending: dict[int, list[str]] = {i: [] for i in ids}
        suspension: dict[int, AccountAudit] = {}
        if ids:
            for uid, role in db.exec(
                select(RoleRequest.user_id, RoleRequest.role)
                .where(col(RoleRequest.user_id).in_(ids), RoleRequest.status == "pending")
                .order_by(col(RoleRequest.created_at))
            ).all():
                pending[uid].append(role)
            suspended = [u.id for u, _, _ in found if u.status == UserStatus.SUSPENDED]
            if suspended:
                for a in db.exec(
                    select(AccountAudit)
                    .where(
                        col(AccountAudit.user_id).in_(suspended), AccountAudit.action == "suspend"
                    )
                    .order_by(col(AccountAudit.at).desc(), col(AccountAudit.id).desc())
                ).all():
                    suspension.setdefault(a.user_id, a)
        rows = []
        for u, last_at, count in found:
            s = suspension.get(u.id)
            rows.append(
                UserRow(
                    id=u.id,
                    email=u.email,
                    name=u.name,
                    picture=u.picture,
                    status=u.status.value,
                    site_role=site_role(u),
                    created_at=_utc(u.created_at),
                    last_login=_utc(u.last_login),
                    approved_at=_utc(u.approved_at),
                    login_count=count or 0,
                    last_sign_in_at=_utc(last_at),
                    pending_requests=pending[u.id],
                    status_reason=s.details.get("reason") if s else None,
                    status_changed_by=s.actor_user_id if s else None,
                    status_changed_at=_utc(s.at) if s else None,
                )
            )
        return rows

    def _row(self, db: Session, user: User) -> UserRow:
        last_at, count = db.exec(
            select(func.max(LoginEvent.at), func.count(LoginEvent.id)).where(
                LoginEvent.user_id == user.id
            )
        ).one()
        return self._rows(db, [(user, last_at, count)])[0]

    def _target(self, db: Session, user_id: int, *, lock: bool = False) -> User:
        stmt = select(User).where(User.id == user_id)
        if lock:
            stmt = stmt.with_for_update()
        user = db.exec(stmt).first()
        if user is None:
            raise NotFound("No such user.")
        return user

    # -- user admin -------------------------------------------------------------

    def list_users(
        self,
        db: Session,
        caller: Caller,
        *,
        q: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> UserPage:
        self._admin(caller)
        filters = []
        if q and q.strip():
            pattern = _like(q.strip())
            filters.append(
                or_(
                    col(User.email).ilike(pattern, escape="\\"),
                    col(User.name).ilike(pattern, escape="\\"),
                )
            )
        if status:
            try:
                filters.append(User.status == UserStatus(status))
            except ValueError:
                raise Invalid(f"Unknown status {status!r}.", field="status") from None
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        stats = (
            select(
                col(LoginEvent.user_id).label("uid"),
                func.max(LoginEvent.at).label("last_at"),
                func.count(LoginEvent.id).label("n"),
            )
            .group_by(col(LoginEvent.user_id))
            .subquery()
        )
        total = db.exec(select(func.count()).select_from(User).where(*filters)).one()
        found = db.exec(
            select(User, stats.c.last_at, stats.c.n)
            .outerjoin(stats, stats.c.uid == User.id)
            .where(*filters)
            .order_by(func.coalesce(stats.c.last_at, User.last_login).desc(), col(User.id).desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return UserPage(users=self._rows(db, [tuple(r) for r in found]), total=total)

    def get_user(self, db: Session, caller: Caller, user_id: int) -> UserDetail:
        self._admin(caller)
        user = self._target(db, user_id)
        logins = db.exec(
            select(LoginEvent)
            .where(LoginEvent.user_id == user_id)
            .order_by(col(LoginEvent.at).desc(), col(LoginEvent.id).desc())
            .limit(50)
        ).all()
        audit = db.exec(
            select(AccountAudit)
            .where(AccountAudit.user_id == user_id)
            .order_by(col(AccountAudit.at).desc(), col(AccountAudit.id).desc())
            .limit(50)
        ).all()
        return UserDetail(
            user=self._row(db, user),
            logins=[LoginEventOut.of(e) for e in logins],
            audit=[AuditOut.of(a) for a in audit],
            role_requests=self._requests(db, RoleRequest.user_id == user_id),
        )

    def approve_user(self, db: Session, caller: Caller, user_id: int) -> UserRow:
        self._admin(caller)
        with atomic_transaction(db):
            user = self._target(db, user_id, lock=True)
            if site_role(user):
                raise EnvAdmin("Site admins are set by environment variables, not here.")
            if user.status != UserStatus.APPROVED:
                old = user.status.value
                user.status = UserStatus.APPROVED
                user.approved_by = caller.user.id
                user.approved_at = _utcnow()
                db.add(user)
                _audit(db, caller, user.id, "approve", {"from": old})
        db.refresh(user)
        return self._row(db, user)

    def suspend_user(self, db: Session, caller: Caller, user_id: int, reason: str) -> UserRow:
        self._admin(caller)
        reason = (reason or "").strip()
        if not reason or len(reason) > MAX_REASON:
            raise Invalid(f"Give a reason of 1 to {MAX_REASON} characters.", field="reason")
        with atomic_transaction(db):
            user = self._target(db, user_id, lock=True)
            if user.id == caller.user.id:
                raise SelfAction("You cannot suspend yourself.")
            if site_role(user):
                raise EnvAdmin(
                    "Site admins are set by environment variables and cannot be suspended."
                )
            if user.status == UserStatus.SUSPENDED:
                raise AlreadySuspended("This account is already suspended.")
            old = user.status.value
            user.status = UserStatus.SUSPENDED
            db.add(user)
            db.exec(
                update(RefreshToken)
                .where(RefreshToken.user_id == user.id, col(RefreshToken.revoked).is_(False))
                .values(revoked=True),
                execution_options={"synchronize_session": False},
            )
            db.exec(
                update(ExchangeCode)
                .where(ExchangeCode.user_id == user.id, col(ExchangeCode.used).is_(False))
                .values(used=True),
                execution_options={"synchronize_session": False},
            )
            _audit(db, caller, user.id, "suspend", {"reason": reason, "from": old})
        db.refresh(user)
        return self._row(db, user)

    def unsuspend_user(self, db: Session, caller: Caller, user_id: int) -> UserRow:
        self._admin(caller)
        with atomic_transaction(db):
            user = self._target(db, user_id, lock=True)
            if user.status != UserStatus.SUSPENDED:
                raise NotSuspended("This account is not suspended.")
            user.status = UserStatus.APPROVED
            db.add(user)
            _audit(db, caller, user.id, "unsuspend", {})
        db.refresh(user)
        return self._row(db, user)

    def list_login_events(
        self, db: Session, caller: Caller, *, user_id: int | None = None, limit: int = 100
    ) -> list[LoginEventOut]:
        self._admin(caller)
        stmt = select(LoginEvent)
        if user_id is not None:
            stmt = stmt.where(LoginEvent.user_id == user_id)
        stmt = stmt.order_by(col(LoginEvent.at).desc(), col(LoginEvent.id).desc()).limit(
            max(1, min(limit, 500))
        )
        return [LoginEventOut.of(e) for e in db.exec(stmt).all()]

    # -- role requests ----------------------------------------------------------

    def _requests(self, db: Session, *where, oldest_first: bool = False) -> list[RoleRequestOut]:
        order = col(RoleRequest.created_at), col(RoleRequest.id)
        if not oldest_first:
            order = tuple(c.desc() for c in order)
        rows = db.exec(
            select(RoleRequest, User.email, User.name)
            .join(User, User.id == RoleRequest.user_id)
            .where(*where)
            .order_by(*order)
        ).all()
        return [self._request_out(r, email, name) for r, email, name in rows]

    def _request_out(self, r: RoleRequest, email: str, name: str) -> RoleRequestOut:
        return RoleRequestOut(
            id=r.id,
            user_id=r.user_id,
            role=r.role,
            role_label=self._label(r.role),
            user_email=email,
            user_name=name,
            status=r.status,
            message=r.message,
            details=r.details or {},
            created_at=_utc(r.created_at),
            decided_at=_utc(r.decided_at),
            decided_by=r.decided_by,
            decision_note=r.decision_note,
        )

    def _one_request(self, db: Session, request_id: int) -> RoleRequestOut:
        return self._requests(db, RoleRequest.id == request_id)[0]

    def available_roles(self, db: Session, caller: Caller) -> RolesOut:
        user = caller.user
        return RolesOut(
            site_role=site_role(user),
            roles=[RoleName(name=n, label=s.label) for n, s in self.hooks.roles.items()],
            held=[n for n, s in self.hooks.roles.items() if s.holds(db, user)],
            pending=self._requests(
                db, RoleRequest.user_id == user.id, RoleRequest.status == "pending"
            ),
        )

    def submit_role_request(
        self, db: Session, caller: Caller, role: str, message: str = "", details: Any = None
    ) -> RoleRequestOut:
        if caller.via != "session":
            raise SessionRequired("Role requests need a browser session.")
        spec = self.hooks.roles.get(role)
        if spec is None:
            raise UnknownRole(f"No role named {role!r}.")
        user = caller.user
        status = user.status
        if status in (UserStatus.SUSPENDED, UserStatus.DENIED) or (
            status == UserStatus.PENDING and not spec.open_to_pending
        ):
            raise AccountNotEligible("Your account cannot request this role.")
        message = message or ""
        if not isinstance(message, str) or len(message) > MAX_MESSAGE:
            raise Invalid(f"The message must be at most {MAX_MESSAGE} characters.", field="message")
        details = {} if details is None else details
        if not isinstance(details, dict):
            raise Invalid("details must be a JSON object.", field="details")
        if len(json.dumps(details)) > MAX_DETAILS_BYTES:
            raise Invalid("details is too large.", field="details")
        if spec.holds(db, user):
            raise AlreadyHolds(f"You already hold the {spec.label} role.")
        if self._has_pending(db, user.id, role):
            raise RequestPending("You already have a pending request for this role.")
        details = spec.validate(db, user, details)
        request = RoleRequest(user_id=user.id, role=role, message=message, details=details)
        db.add(request)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise RequestPending("You already have a pending request for this role.") from None
        db.refresh(request)
        return self._request_out(request, user.email, user.name)

    def _has_pending(self, db: Session, user_id: int, role: str) -> bool:
        return (
            db.exec(
                select(RoleRequest.id).where(
                    RoleRequest.user_id == user_id,
                    RoleRequest.role == role,
                    RoleRequest.status == "pending",
                )
            ).first()
            is not None
        )

    def my_role_requests(self, db: Session, caller: Caller) -> list[RoleRequestOut]:
        return self._requests(db, RoleRequest.user_id == caller.user.id)

    def _lock_request(self, db: Session, request_id: int) -> RoleRequest | None:
        return db.exec(
            select(RoleRequest).where(RoleRequest.id == request_id).with_for_update()
        ).first()

    def withdraw_role_request(self, db: Session, caller: Caller, request_id: int) -> None:
        with atomic_transaction(db):
            request = self._lock_request(db, request_id)
            if request is None or request.user_id != caller.user.id:
                raise NotYourRequest("No such request.")
            if request.status != "pending":
                raise NotPending("This request is no longer pending.")
            request.status = "withdrawn"
            request.decided_at = _utcnow()
            request.decided_by = caller.user.id
            db.add(request)
            _audit(
                db,
                caller,
                request.user_id,
                "role_request_withdraw",
                {"request_id": request.id, "role": request.role},
            )

    def list_role_requests(
        self,
        db: Session,
        caller: Caller,
        *,
        status: str | None = "pending",
        role: str | None = None,
    ) -> list[RoleRequestOut]:
        """``status`` is one of REQUEST_STATUSES, or ``"all"``/None for every status."""
        self._admin(caller)
        where = []
        if status and status != "all":
            if status not in REQUEST_STATUSES:
                raise Invalid(f"Unknown status {status!r}.", field="status")
            where.append(RoleRequest.status == status)
        if role:
            where.append(RoleRequest.role == role)
        return self._requests(db, *where, oldest_first=status == "pending")

    def _decide(self, db: Session, request_id: int) -> RoleRequest:
        request = self._lock_request(db, request_id)
        if request is None:
            raise NotFound("No such request.")
        if request.status != "pending":
            raise NotPending("This request is no longer pending.")
        return request

    @staticmethod
    def _note(note: str | None) -> str | None:
        note = (note or "").strip() or None
        if note and len(note) > MAX_NOTE:
            raise Invalid(f"The note must be at most {MAX_NOTE} characters.", field="note")
        return note

    def approve_role_request(
        self, db: Session, caller: Caller, request_id: int, note: str | None = None
    ) -> RoleRequestOut:
        self._admin(caller)
        note = self._note(note)
        with atomic_transaction(db):
            request = self._decide(db, request_id)
            spec = self.hooks.roles.get(request.role)
            if spec is None:
                raise UnknownRole(f"The role {request.role!r} is no longer offered.")
            user = self._target(db, request.user_id)
            if user.status in (UserStatus.SUSPENDED, UserStatus.DENIED):
                raise AccountNotEligible("This account is suspended or denied; unsuspend it first.")
            request.details = spec.validate(db, user, dict(request.details or {}))
            audit = {"request_id": request.id, "role": request.role, "note": note}
            if spec.holds(db, user):
                audit["already_held"] = True
            else:
                spec.on_approve(db, request)
            request.status = "approved"
            request.decided_at = _utcnow()
            request.decided_by = caller.user.id
            request.decision_note = note
            db.add(request)
            _audit(db, caller, request.user_id, "role_request_approve", audit)
        return self._one_request(db, request_id)

    def reject_role_request(
        self, db: Session, caller: Caller, request_id: int, note: str | None = None
    ) -> RoleRequestOut:
        self._admin(caller)
        note = self._note(note)
        with atomic_transaction(db):
            request = self._decide(db, request_id)
            request.status = "rejected"
            request.decided_at = _utcnow()
            request.decided_by = caller.user.id
            request.decision_note = note
            db.add(request)
            _audit(
                db,
                caller,
                request.user_id,
                "role_request_reject",
                {"request_id": request.id, "role": request.role, "note": note},
            )
        return self._one_request(db, request_id)


# =============================================================================
# HTTP
# =============================================================================


class SuspendBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = ""


class NoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = None


class RoleRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    message: str = ""
    details: Any = None  # checked by the service, so a bad shape gets its error body


def _call(fn, *a, **kw):
    """Run a service call; a ServiceError becomes ``{"detail": {"error", "message"[, "field"]}}``."""
    try:
        return fn(*a, **kw)
    except ServiceError as e:
        detail = {"error": e.code, "message": e.detail}
        if isinstance(e, Invalid) and e.field:
            detail["field"] = e.field
        raise HTTPException(int(e.status), detail=detail, headers=http_headers(e) or None) from e


def create_useradmin_router(
    get_db: Callable,
    admin: UserAdmin,
    *,
    get_caller: Callable | None = None,
    api_key_prefix: str | None = None,
    scopes: ScopeConfig | None = None,
) -> APIRouter:
    """The user-admin routes under ``/auth``.

    ``get_caller``: the app's own ``Caller`` dependency (e.g. Pigeon's
    ``require_caller``). None: snapgate's flexible auth (session JWT or API key)
    with ``scopes``. Role-request routes admit any status and leave the rules to
    the service, so an app whose ``get_caller`` admits only APPROVED users makes
    ``open_to_pending`` roles unreachable for pending users. Errors use
    snapgate's auth body ``{"detail": {"error", "message"}}``, whether or not
    the app calls ``install_service_errors``.
    """
    router = APIRouter(prefix="/auth", tags=["User admin"])

    if get_caller is None:
        from .dependencies import create_flexible_auth_dependencies

        _, require_user, _, get_request_api_key = create_flexible_auth_dependencies(
            get_db, api_key_prefix
        )
        scope_config = scopes or ScopeConfig(scopes={}, default_scopes=())

        def default_caller(
            user: User = Depends(require_user), key=Depends(get_request_api_key)
        ) -> Caller:
            return caller_for(user, key, scope_config)

        get_caller = default_caller

    def any_caller(caller: Caller = Depends(get_caller)) -> Caller:
        return caller

    def admin_caller(caller: Caller = Depends(get_caller)) -> Caller:
        _call(require_site_admin, caller, admin.hooks.admin_via)
        return caller

    @router.get("/admin/users", response_model=UserPage)
    def list_users(
        q: str | None = None,
        status: str | None = None,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        db: Session = Depends(get_db),
        caller: Caller = Depends(admin_caller),
    ):
        return _call(admin.list_users, db, caller, q=q, status=status, limit=limit, offset=offset)

    @router.get("/admin/users/{user_id}", response_model=UserDetail)
    def get_user(user_id: int, db: Session = Depends(get_db), caller=Depends(admin_caller)):
        return _call(admin.get_user, db, caller, user_id)

    @router.post("/admin/users/{user_id}/approve", response_model=UserEnvelope)
    def approve_user(user_id: int, db: Session = Depends(get_db), caller=Depends(admin_caller)):
        return UserEnvelope(user=_call(admin.approve_user, db, caller, user_id))

    @router.post("/admin/users/{user_id}/suspend", response_model=UserEnvelope)
    def suspend_user(
        user_id: int,
        body: SuspendBody,
        db: Session = Depends(get_db),
        caller=Depends(admin_caller),
    ):
        return UserEnvelope(user=_call(admin.suspend_user, db, caller, user_id, body.reason))

    @router.post("/admin/users/{user_id}/unsuspend", response_model=UserEnvelope)
    def unsuspend_user(user_id: int, db: Session = Depends(get_db), caller=Depends(admin_caller)):
        return UserEnvelope(user=_call(admin.unsuspend_user, db, caller, user_id))

    @router.get("/admin/logins", response_model=LoginList)
    def list_logins(
        user_id: int | None = None,
        limit: int = Query(100, ge=1, le=500),
        db: Session = Depends(get_db),
        caller=Depends(admin_caller),
    ):
        return LoginList(
            logins=_call(admin.list_login_events, db, caller, user_id=user_id, limit=limit)
        )

    @router.get("/admin/role-requests", response_model=RequestList)
    def list_role_requests(
        status: str = "pending",
        role: str | None = None,
        db: Session = Depends(get_db),
        caller=Depends(admin_caller),
    ):
        return RequestList(
            requests=_call(admin.list_role_requests, db, caller, status=status, role=role)
        )

    @router.post("/admin/role-requests/{request_id}/approve", response_model=RequestEnvelope)
    def approve_role_request(
        request_id: int,
        body: NoteBody | None = None,
        db: Session = Depends(get_db),
        caller=Depends(admin_caller),
    ):
        note = body.note if body else None
        return RequestEnvelope(
            request=_call(admin.approve_role_request, db, caller, request_id, note)
        )

    @router.post("/admin/role-requests/{request_id}/reject", response_model=RequestEnvelope)
    def reject_role_request(
        request_id: int,
        body: NoteBody | None = None,
        db: Session = Depends(get_db),
        caller=Depends(admin_caller),
    ):
        note = body.note if body else None
        return RequestEnvelope(
            request=_call(admin.reject_role_request, db, caller, request_id, note)
        )

    @router.get("/role-requests", response_model=RequestList)
    def my_role_requests(db: Session = Depends(get_db), caller=Depends(any_caller)):
        return RequestList(requests=_call(admin.my_role_requests, db, caller))

    @router.post("/role-requests", response_model=RequestEnvelope, status_code=201)
    def submit_role_request(
        request: Request,
        body: RoleRequestBody,
        db: Session = Depends(get_db),
        caller=Depends(any_caller),
    ):
        enforce_rate_limit(request, "role_request")
        return RequestEnvelope(
            request=_call(
                admin.submit_role_request, db, caller, body.role, body.message, body.details
            )
        )

    @router.delete("/role-requests/{request_id}", status_code=204, response_class=Response)
    def withdraw_role_request(
        request_id: int, db: Session = Depends(get_db), caller=Depends(any_caller)
    ):
        _call(admin.withdraw_role_request, db, caller, request_id)
        return Response(status_code=204)

    @router.get("/roles", response_model=RolesOut)
    def roles(db: Session = Depends(get_db), caller=Depends(any_caller)):
        return _call(admin.available_roles, db, caller)

    return router


__all__ = [
    "AUDIT_ACTIONS",
    "REQUEST_STATUSES",
    "AccountAudit",
    "AccountNotEligible",
    "AlreadyHolds",
    "AlreadySuspended",
    "AuditOut",
    "EnvAdmin",
    "LoginEvent",
    "LoginEventOut",
    "NotAdmin",
    "NotPending",
    "NotSuspended",
    "NotYourRequest",
    "RequestPending",
    "RoleRequest",
    "RoleRequestOut",
    "RoleSpec",
    "RolesOut",
    "SelfAction",
    "SessionRequired",
    "UnknownRole",
    "UserAdmin",
    "UserAdminHooks",
    "UserDetail",
    "UserPage",
    "UserRow",
    "actor_label",
    "create_useradmin_router",
    "record_sign_in",
    "require_site_admin",
    "site_role",
]
