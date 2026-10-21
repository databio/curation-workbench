"""Site role (snapgate) and team role (`project_members`).

Site roles come only from `SNAPGATE_SUPERADMIN_EMAILS` / `SNAPGATE_ADMIN_EMAILS`
(read on every call), never from snapgate's first user, and count only for an
APPROVED account on a browser session: an API key is never admin. Only a
superadmin session creates projects and manages teams.

On a project a caller is a `voter`, a `curator` (who can also vote), or not
on the team. A project the caller is not on is `NotFound` (404), never 403.
A superadmin session can read every project (for admin) but votes and
decides only where it is also on the team.

`OPERATOR` is the `cwb hosted admin ...` command line: a superadmin for the
admin functions, with no user and no team role.
"""
from __future__ import annotations

from snapgate import Caller, NotFound, User, UserStatus, useradmin
from sqlalchemy import select

from ...store.pg.tables import projects
from ..scopes import DECIDE, READ, VOTE
from ..tables import project_members
from .errors import NotAdmin, NotCurator, NotVoter, SessionRequired

ROLES = ("voter", "curator")


class _Operator:
    """The operator command line (`cwb hosted admin ...`)."""
    user = None
    via = "operator"

    def __repr__(self):
        return "OPERATOR"


OPERATOR = _Operator()


def site_role(user: User | None) -> str | None:
    """`"superadmin"` | `"admin"` | None, from snapgate's env lists; None unless APPROVED."""
    if user is None or getattr(user, "status", None) != UserStatus.APPROVED:
        return None
    return useradmin.site_role(user)


def is_superadmin(caller) -> bool:
    """A superadmin browser session (or the operator CLI)."""
    if caller is OPERATOR:
        return True
    return (isinstance(caller, Caller) and caller.via == "session"
            and site_role(caller.user) == "superadmin")


def require_superadmin(caller) -> None:
    if caller is OPERATOR:
        return
    if not isinstance(caller, Caller) or caller.via != "session":
        raise SessionRequired("Admin actions need a browser session.")
    if site_role(caller.user) != "superadmin":
        raise NotAdmin("Only a superadmin can do that.")


def get_project(conn, slug, include_archived=False) -> dict | None:
    q = select(projects.c.id, projects.c.slug, projects.c.name, projects.c.archived_at,
               projects.c.created_at).where(projects.c.slug == slug)
    if not include_archived:
        q = q.where(projects.c.archived_at.is_(None))
    row = conn.execute(q).one_or_none()
    return dict(row._mapping) if row else None


def team_role(conn, user_id, project_id) -> str | None:
    if user_id is None:
        return None
    return conn.execute(select(project_members.c.role).where(
        project_members.c.project_id == project_id, project_members.c.user_id == user_id,
        project_members.c.removed_at.is_(None))).scalar_one_or_none()


def memberships(conn, user_id) -> dict[str, str]:
    """{slug: role} for every live project the user is on."""
    rows = conn.execute(
        select(projects.c.slug, project_members.c.role)
        .join(projects, projects.c.id == project_members.c.project_id)
        .where(project_members.c.user_id == user_id, project_members.c.removed_at.is_(None),
               projects.c.archived_at.is_(None))
        .order_by(projects.c.slug))
    return {r.slug: r.role for r in rows}


def active_members(conn, project_id) -> dict[int, str]:
    """{user_id: role} of the current team."""
    rows = conn.execute(select(project_members.c.user_id, project_members.c.role).where(
        project_members.c.project_id == project_id, project_members.c.removed_at.is_(None)))
    return {r.user_id: r.role for r in rows}


#: The scope each team need implies.
SCOPE_FOR = {None: READ, "voter": VOTE, "curator": DECIDE}


def access(conn, caller: Caller, slug: str, need: str | None = None,
           scope: str | None = None) -> tuple[dict, str | None]:
    """Scope first, then the team role. Returns (project, role).

    `need`: None (read: any member, or a superadmin session), "voter"
    (voter or curator) or "curator". A project the caller cannot see is
    NotFound; one they can see but may not act on is NotVoter / NotCurator."""
    caller.require(scope or SCOPE_FOR[need])
    project = get_project(conn, slug)
    if project is None:
        raise NotFound(f"no project {slug}")
    role = team_role(conn, caller.user.id, project["id"])
    if role is None and not is_superadmin(caller):
        raise NotFound(f"no project {slug}")
    if need == "voter" and role not in ROLES:
        raise NotVoter(f"you are not on the {slug} team")
    if need == "curator" and role != "curator":
        raise NotCurator(f"only a {slug} curator can do that")
    return project, role


__all__ = ["OPERATOR", "ROLES", "access", "active_members", "get_project", "is_superadmin",
           "memberships", "require_superadmin", "site_role", "team_role"]
