"""A hosted project's data, read from plan 4's tables. Never files, git or plugin code.

`load(conn, slug)` -> `ProjectData`: the project row, its resident's current
snapshot (`cw_snapshots.bundle`, decoded and cached in process memory by
snapshot id; snapshots are immutable) and the derived status/review
(`cw_derived`, read fresh on every request: it changes on every vote). No
resident or no current snapshot raises `NotSynced` (503 `not_synced`).
`context(pd)` is the `ValidationContext` every hosted write validates against
(plugin decision checks are the worker's asynchronous `decision_checks`).
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass

from snapgate import NotFound
from sqlalchemy import select

from ..store import Subjects, ValidationContext
from ..store.pg.tables import projects
from .service.errors import NotSynced
from .sync.publish import decode_bundle
from .sync.tables import cw_derived, cw_residents, cw_snapshots

_CACHE_SIZE = 8
_cache: OrderedDict[int, tuple[dict, Subjects]] = OrderedDict()
_lock = threading.Lock()


@dataclass(frozen=True)
class ProjectData:
    project: dict
    resident_id: int
    snapshot_id: int
    commit: str
    branch: str
    schema_fingerprint: str
    synced_at: str
    bundle: dict
    status: dict
    review: dict | None
    decision_checks: dict
    subjects: Subjects


def project_row(conn, slug, include_archived=False) -> dict | None:
    q = select(projects.c.id, projects.c.slug, projects.c.name, projects.c.archived_at,
               projects.c.created_at).where(projects.c.slug == slug)
    if not include_archived:
        q = q.where(projects.c.archived_at.is_(None))
    row = conn.execute(q).one_or_none()
    return dict(row._mapping) if row else None


def _bundle(conn, snapshot_id):
    with _lock:
        hit = _cache.get(snapshot_id)
        if hit is not None:
            _cache.move_to_end(snapshot_id)
            return hit
    data = conn.execute(select(cw_snapshots.c.bundle)
                        .where(cw_snapshots.c.id == snapshot_id)).scalar_one()
    bundle = decode_bundle(data)
    entry = (bundle, Subjects(bundle["subjects"]))
    with _lock:
        _cache[snapshot_id] = entry
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return entry


def sync_info(conn, project_id) -> dict:
    """{resident_id, snapshot_id, commit, branch, schema_fingerprint, synced_at};
    all None before the first sync."""
    row = conn.execute(
        select(cw_residents.c.id, cw_residents.c.branch, cw_residents.c.current_snapshot_id,
               cw_snapshots.c.commit_sha, cw_snapshots.c.schema_fingerprint, cw_snapshots.c.created_at)
        .select_from(cw_residents.outerjoin(cw_snapshots,
                                            cw_snapshots.c.id == cw_residents.c.current_snapshot_id))
        .where(cw_residents.c.project_id == project_id)).one_or_none()
    if row is None:
        return {"resident_id": None, "snapshot_id": None, "commit": None, "branch": None,
                "schema_fingerprint": None, "synced_at": None}
    return {"resident_id": row.id, "snapshot_id": row.current_snapshot_id, "commit": row.commit_sha,
            "branch": row.branch, "schema_fingerprint": row.schema_fingerprint,
            "synced_at": row.created_at.isoformat() if row.created_at else None}


def load_for(conn, project: dict) -> ProjectData:
    info = sync_info(conn, project["id"])
    if info["snapshot_id"] is None:
        raise NotSynced(f"project {project['slug']} has no published snapshot yet")
    derived = conn.execute(select(cw_derived.c.status, cw_derived.c.review,
                                  cw_derived.c.decision_checks)
                           .where(cw_derived.c.resident_id == info["resident_id"])).one_or_none()
    if derived is None:
        raise NotSynced(f"project {project['slug']} has no derived status yet")
    bundle, subjects = _bundle(conn, info["snapshot_id"])
    return ProjectData(project=project, resident_id=info["resident_id"],
                       snapshot_id=info["snapshot_id"], commit=info["commit"], branch=info["branch"],
                       schema_fingerprint=info["schema_fingerprint"], synced_at=info["synced_at"],
                       bundle=bundle, status=derived.status, review=derived.review,
                       decision_checks=derived.decision_checks or {}, subjects=subjects)


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
    with _lock:
        _cache.clear()


__all__ = ["ProjectData", "clear_cache", "context", "load", "load_for", "project_row", "sync_info"]
