"""A hosted project's data: its current snapshot plus status and review derived
on read. Never files, git or plugin code.

`load(conn, slug)` -> `ProjectData`: the project row, the current snapshot's
bundle (from `cw_snapshots`, cached in memory by snapshot id; snapshots never
change) and status/review from `derive` over the bundle and the merged
decisions. Derived data is cached by (snapshot id, `decisions_rev`): every
decision write bumps the rev, and a derive takes milliseconds. No repo or no
snapshot yet raises `NotSynced` (503 `not_synced`). `context(pd)` is the
`ValidationContext` every hosted write validates against.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass

from snapgate import NotFound
from sqlalchemy import select

from .. import derive
from ..store import Subjects, ValidationContext
from ..store.pg import PgStore
from ..store.pg.tables import projects
from .service.errors import NotSynced
from .sync import publish
from .sync.repos import repo_info

_CACHE_SIZE = 16
_derived: OrderedDict[tuple, tuple] = OrderedDict()
_subjects: dict[int, Subjects] = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class ProjectData:
    project: dict
    snapshot_id: int
    commit: str
    branch: str
    repo: str
    schema_fingerprint: str
    synced_at: str
    bundle: dict
    status: dict
    review: dict | None
    subjects: Subjects


def project_row(conn, slug, include_archived=False) -> dict | None:
    q = select(projects.c.id, projects.c.slug, projects.c.name, projects.c.archived_at,
               projects.c.created_at).where(projects.c.slug == slug)
    if not include_archived:
        q = q.where(projects.c.archived_at.is_(None))
    row = conn.execute(q).one_or_none()
    return dict(row._mapping) if row else None


def sync_info(conn, project_id) -> dict:
    """{repo, branch, snapshot_id, commit, schema_fingerprint, synced_at,
    last_checked_at, last_error, warnings}; None values before the first fetch."""
    return repo_info(conn, project_id)


def _subjects_of(snapshot_id, bundle) -> Subjects:
    with _lock:
        s = _subjects.get(snapshot_id)
        if s is None:
            if len(_subjects) > _CACHE_SIZE:
                _subjects.clear()
            s = _subjects[snapshot_id] = Subjects(bundle["subjects"])
        return s


def derived(conn, project_id, snapshot_id, bundle) -> tuple[dict, dict | None]:
    """(status, review) for the snapshot and the project's decisions now."""
    store = PgStore(conn.engine, project_id)
    with store.using(conn):
        snap = store.snapshot()          # reads the rev before the records
    key = (snapshot_id, snap["token"])
    with _lock:
        hit = _derived.get(key)
        if hit is not None:
            _derived.move_to_end(key)
            return hit
    decisions = derive.merge_decisions(bundle.get("repo_decisions") or [], snap["decisions"])
    st = derive.status(bundle, decisions)
    rv = derive.review(bundle, st, f"H-{int(snap['next_id']):04d}")
    with _lock:
        _derived[key] = (st, rv)
        while len(_derived) > _CACHE_SIZE:
            _derived.popitem(last=False)
    return st, rv


def load_for(conn, project: dict) -> ProjectData:
    info = sync_info(conn, project["id"])
    if info["snapshot_id"] is None:
        raise NotSynced(f"project {project['slug']} has no snapshot yet"
                        + (f" ({info['last_error']})" if info["last_error"] else ""))
    bundle = publish.load_bundle(conn, info["snapshot_id"])
    st, rv = derived(conn, project["id"], info["snapshot_id"], bundle)
    return ProjectData(project=project, snapshot_id=info["snapshot_id"], commit=info["commit"],
                       branch=info["branch"], repo=info["repo"],
                       schema_fingerprint=info["schema_fingerprint"], synced_at=info["synced_at"],
                       bundle=bundle, status=st, review=rv,
                       subjects=_subjects_of(info["snapshot_id"], bundle))


def load(conn, slug) -> ProjectData:
    project = project_row(conn, slug)
    if project is None:
        raise NotFound(f"no project {slug}")
    return load_for(conn, project)


def context(pd: ProjectData) -> ValidationContext:
    b = pd.bundle
    return ValidationContext(
        subjects=pd.subjects, verdicts=b.get("verdicts") or {}, wb=None,
        review_types=b.get("review_types"),
        extra_known_ids=frozenset(r["id"] for r in b.get("repo_decisions") or []))


def clear_cache():
    publish.clear_cache()
    with _lock:
        _derived.clear()
        _subjects.clear()


__all__ = ["ProjectData", "clear_cache", "context", "derived", "load", "load_for", "project_row",
           "sync_info"]
