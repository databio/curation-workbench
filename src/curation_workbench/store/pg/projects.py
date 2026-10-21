"""Hosted curation projects: one row each in `projects`.

Plan 2's admin routes and `cwb db project add` call these. Each returns
{id, slug, name, archived_at, created_at} (timestamps as ISO strings).
"""
from __future__ import annotations

import re

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from ..validate import StoreError
from .tables import projects

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
_COLS = (projects.c.id, projects.c.slug, projects.c.name, projects.c.archived_at,
         projects.c.created_at)


def _out(row) -> dict:
    return {"id": row.id, "slug": row.slug, "name": row.name,
            "archived_at": row.archived_at.isoformat() if row.archived_at else None,
            "created_at": row.created_at.isoformat()}


def _check_name(name):
    if not isinstance(name, str) or not name.strip():
        raise StoreError(400, ["a project needs a name"])


def create_project(engine, slug: str, name: str) -> dict:
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise StoreError(400, [f"project slug must match {SLUG_RE.pattern}, got {slug!r}"])
    _check_name(name)
    try:
        with engine.begin() as conn:
            row = conn.execute(projects.insert().values(slug=slug, name=name)
                               .returning(*_COLS)).one()
    except IntegrityError:
        raise StoreError(409, [f"project {slug} already exists"]) from None
    return _out(row)


def get_project(engine, slug: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(select(*_COLS).where(projects.c.slug == slug)).one_or_none()
    return _out(row) if row else None


def list_projects(engine, include_archived: bool = False) -> list[dict]:
    q = select(*_COLS).order_by(projects.c.slug)
    if not include_archived:
        q = q.where(projects.c.archived_at.is_(None))
    with engine.connect() as conn:
        return [_out(r) for r in conn.execute(q)]


def _set(engine, slug, **values) -> dict:
    with engine.begin() as conn:
        row = conn.execute(update(projects).where(projects.c.slug == slug)
                           .values(updated_at=func.now(), **values)
                           .returning(*_COLS)).one_or_none()
    if row is None:
        raise StoreError(404, [f"no project {slug}"])
    return _out(row)


def archive_project(engine, slug: str) -> dict:
    """Mark a project archived (kept, hidden from `list_projects`). Idempotent."""
    cur = get_project(engine, slug)
    if cur is None:
        raise StoreError(404, [f"no project {slug}"])
    if cur["archived_at"]:
        return cur
    return _set(engine, slug, archived_at=func.now())


def rename_project(engine, slug: str, name: str) -> dict:
    _check_name(name)
    return _set(engine, slug, name=name)


__all__ = ["SLUG_RE", "archive_project", "create_project", "get_project", "list_projects",
           "rename_project"]
