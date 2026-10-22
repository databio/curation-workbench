"""Who am I, the project list and detail, the team, and project + team admin.

Project and team admin is superadmin-session only (`roles.require_superadmin`).
Adding a member needs an APPROVED account, so "approve, then add" is enforced.
"""
from __future__ import annotations

from snapgate import Caller, Invalid, NotFound, User, UserStatus
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ...store import StoreError
from ...store.pg import projects as pg_projects
from ...store.pg.tables import decisions, projects
from .. import project_data
from ..sync import repos
from ..tables import project_members
from . import roles
from .errors import AccountNotApproved, BadRequest

USERS = User.__table__


def _user_out(u) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email}


def me(engine, caller: Caller) -> dict:
    with engine.connect() as conn:
        mem = roles.memberships(conn, caller.user.id)
    return {"user": _user_out(caller.user),
            "site_role": roles.site_role(caller.user) if caller.via == "session" else None,
            "memberships": mem}


def _summary(conn, project, role) -> dict:
    info = project_data.sync_info(conn, project["id"])
    return {"slug": project["slug"], "name": project["name"], "role": role,
            "commit": info["commit"], "schema_fingerprint": info["schema_fingerprint"],
            "synced_at": info["synced_at"]}


def list_projects(engine, caller: Caller) -> dict:
    """The projects the caller is on (every live project, for a superadmin session)."""
    caller.require(roles.SCOPE_FOR[None])
    with engine.connect() as conn:
        mine = roles.memberships(conn, caller.user.id)
        rows = conn.execute(select(projects.c.id, projects.c.slug, projects.c.name)
                            .where(projects.c.archived_at.is_(None)).order_by(projects.c.slug))
        everything = roles.is_superadmin(caller)
        return {"projects": [_summary(conn, dict(r._mapping), mine.get(r.slug))
                             for r in rows if everything or r.slug in mine]}


def _open_questions(conn, project_id) -> int:
    from .votes import question_states
    return sum(1 for q in question_states(conn, project_id).values() if q["state"] == "open")


def project_detail(engine, caller: Caller, slug: str) -> dict:
    with engine.connect() as conn:
        project, role = roles.access(conn, caller, slug)
        out = _summary(conn, project, role)
        out["branch"] = project_data.sync_info(conn, project["id"])["branch"]
        counts = dict(conn.execute(select(decisions.c.status, func.count())
                                   .where(decisions.c.project_id == project["id"])
                                   .group_by(decisions.c.status)).all())
        out["decision_counts"] = {k: counts.get(k, 0) for k in ("pending", "applied", "withdrawn")}
        out["open_questions"] = _open_questions(conn, project["id"])
    return {k: out[k] for k in ("slug", "name", "role", "commit", "branch", "schema_fingerprint",
                                "synced_at", "decision_counts", "open_questions")}


def _members(conn, project_id) -> list[dict]:
    rows = conn.execute(
        select(project_members.c.user_id, project_members.c.role, USERS.c.name, USERS.c.email)
        .join(USERS, USERS.c.id == project_members.c.user_id)
        .where(project_members.c.project_id == project_id, project_members.c.removed_at.is_(None))
        .order_by(USERS.c.name, USERS.c.id))
    return [{"user_id": r.user_id, "name": r.name, "email": r.email, "role": r.role} for r in rows]


def team(engine, caller: Caller, slug: str) -> dict:
    with engine.connect() as conn:
        project, _ = roles.access(conn, caller, slug)
        return {"members": _members(conn, project["id"])}


# ------------------------------------------------------------------ admin

def _admin_row(conn, p) -> dict:
    info = project_data.sync_info(conn, p["id"])
    return {"slug": p["slug"], "name": p["name"], "archived_at": p["archived_at"],
            "created_at": p["created_at"], "repo": info["repo"], "branch": info["branch"],
            "commit": info["commit"], "synced_at": info["synced_at"],
            "last_checked_at": info["last_checked_at"], "last_error": info["last_error"],
            "members": len(roles.active_members(conn, p["id"]))}


def admin_list_projects(engine, caller) -> dict:
    roles.require_superadmin(caller)
    rows = pg_projects.list_projects(engine, include_archived=True)
    with engine.connect() as conn:
        return {"projects": [_admin_row(conn, p) for p in rows]}


def _body_str(body, key):
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    v = body.get(key)
    if not isinstance(v, str):
        raise BadRequest(f"{key} is required")
    return v


def admin_create_project(engine, caller, body, *, gh=None) -> dict:
    """Create a project. With `repo` (a public GitHub repo) and optional
    `branch` (default master), also register the repo and load its snapshot."""
    roles.require_superadmin(caller)
    slug, name = _body_str(body, "slug"), _body_str(body, "name")
    repo = body.get("repo")
    if repo:
        if not isinstance(repo, str):
            raise BadRequest("repo must be a string")
        branch = body.get("branch") or "master"
        if not isinstance(branch, str):
            raise BadRequest("branch must be a string")
        if pg_projects.get_project(engine, slug) is not None:
            raise StoreError(409, [f"project {slug} already exists"])
        out = repos.register(engine, caller, slug, name, repo, branch, gh=gh)
        p = out["project"]
    else:
        p = pg_projects.create_project(engine, slug, name)
    with engine.connect() as conn:
        return {"project": _admin_row(conn, p)}


def admin_set_repo(engine, caller, slug, body, *, gh=None) -> dict:
    """Set (or change) a project's GitHub repo and branch, then load its snapshot."""
    roles.require_superadmin(caller)
    p = pg_projects.get_project(engine, slug)
    if p is None:
        raise StoreError(404, [f"no project {slug}"])
    repo = _body_str(body, "repo")
    repos.register(engine, caller, slug, p["name"], repo, body.get("branch") or "master", gh=gh)
    with engine.connect() as conn:
        return {"project": _admin_row(conn, p)}


def admin_sync_project(engine, caller, slug, *, gh=None) -> dict:
    """Fetch the project's snapshot from GitHub now."""
    roles.require_superadmin(caller)
    result = repos.sync_project(engine, slug, gh=gh)
    p = pg_projects.get_project(engine, slug)
    with engine.connect() as conn:
        return {"project": _admin_row(conn, p), "fetch": result}


def admin_rename_project(engine, caller, slug, body) -> dict:
    roles.require_superadmin(caller)
    p = pg_projects.rename_project(engine, slug, _body_str(body, "name"))
    with engine.connect() as conn:
        return {"project": _admin_row(conn, p)}


def admin_archive_project(engine, caller, slug) -> dict:
    roles.require_superadmin(caller)
    p = pg_projects.archive_project(engine, slug)
    with engine.connect() as conn:
        return {"project": _admin_row(conn, p)}


def _live_project(conn, slug) -> dict:
    p = roles.get_project(conn, slug)
    if p is None:
        raise StoreError(404, [f"no project {slug}"])
    return p


def set_member(engine, caller, slug: str, user_id: int, role: str) -> dict:
    """Add an APPROVED account to the team, or change its role (a removed
    member comes back)."""
    roles.require_superadmin(caller)
    if role not in roles.ROLES:
        raise Invalid(f"role must be one of {list(roles.ROLES)}", field="role")
    with engine.begin() as conn:
        project = _live_project(conn, slug)
        user = conn.execute(select(USERS.c.id, USERS.c.name, USERS.c.email, USERS.c.status)
                            .where(USERS.c.id == user_id)).one_or_none()
        if user is None:
            raise NotFound(f"no account {user_id}")
        if UserStatus(user.status) != UserStatus.APPROVED:
            raise AccountNotApproved(
                f"{user.email} is {UserStatus(user.status).value}; approve the account first")
        added_by = caller.user.id if caller is not roles.OPERATOR else None
        stmt = pg_insert(project_members).values(project_id=project["id"], user_id=user_id,
                                                 role=role, added_by=added_by)
        conn.execute(stmt.on_conflict_do_update(
            index_elements=[project_members.c.project_id, project_members.c.user_id],
            set_={"role": role, "removed_at": None, "added_by": added_by,
                  "added_at": func.now()}))
    return {"member": {"user_id": user.id, "name": user.name, "email": user.email, "role": role}}


def remove_member(engine, caller, slug: str, user_id: int) -> None:
    """Take someone off the team. Their votes stay, shown as a former member's
    and not counted."""
    roles.require_superadmin(caller)
    with engine.begin() as conn:
        project = _live_project(conn, slug)
        done = conn.execute(update(project_members).where(
            project_members.c.project_id == project["id"], project_members.c.user_id == user_id,
            project_members.c.removed_at.is_(None)).values(removed_at=func.now())).rowcount
    if not done:
        raise NotFound(f"account {user_id} is not on the {slug} team")


def user_id_for_email(engine, email: str) -> int:
    with engine.connect() as conn:
        uid = conn.execute(select(USERS.c.id).where(func.lower(USERS.c.email) == email.strip().lower())
                           ).scalar_one_or_none()
    if uid is None:
        raise NotFound(f"no account {email} (they must sign in once first)")
    return uid


__all__ = ["admin_archive_project", "admin_create_project", "admin_list_projects",
           "admin_rename_project", "admin_set_repo", "admin_sync_project", "list_projects", "me", "project_detail", "remove_member",
           "set_member", "team", "user_id_for_email"]
