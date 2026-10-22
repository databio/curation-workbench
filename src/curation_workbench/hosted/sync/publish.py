"""Publishing a snapshot, and the staleness pass after each write.

`publish_snapshot` is one transaction: insert (or reuse) the `cw_snapshots`
row for the commit, mark hosted decisions that reached the repo as applied
(`mark_landed`), run the staleness pass, and make the snapshot current. A
failure leaves the last good snapshot live. Status and review are not stored:
`project_data` derives them on read from the bundle and the decisions.

`refresh_stale(conn, project_id)` runs the staleness pass against the current
snapshot; the vote and decision services call it in each write's transaction.
"""
from __future__ import annotations

import datetime
import gzip
import hashlib
import json
import threading
from collections import OrderedDict

from sqlalchemy import func, select, update

from ...snapshot import check_format
from ...store.landing import landed_refs
from ...store.pg import PgStore
from . import stale
from .tables import cw_repos, cw_snapshots

_CACHE_SIZE = 8
_cache: OrderedDict[int, dict] = OrderedDict()
_lock = threading.Lock()


def encode_bundle(bundle):
    raw = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode()
    return gzip.compress(raw, mtime=0), hashlib.sha256(raw).hexdigest()


def decode_bundle(data):
    return json.loads(gzip.decompress(data))


def load_bundle(conn, snapshot_id):
    """The bundle of one snapshot, cached in process memory (snapshots never change)."""
    with _lock:
        hit = _cache.get(snapshot_id)
        if hit is not None:
            _cache.move_to_end(snapshot_id)
            return hit
    bundle = decode_bundle(conn.execute(select(cw_snapshots.c.bundle)
                                        .where(cw_snapshots.c.id == snapshot_id)).scalar_one())
    with _lock:
        _cache[snapshot_id] = bundle
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return bundle


def clear_cache():
    with _lock:
        _cache.clear()


def find_snapshot(conn, project_id, commit_sha):
    return conn.execute(select(cw_snapshots.c.id).where(
        cw_snapshots.c.project_id == project_id,
        cw_snapshots.c.commit_sha == commit_sha)).scalar_one_or_none()


def mark_landed(store, bundle, commit_sha):
    """Mark every pending hosted decision that the repo now holds (a repo
    record with `origin: {kind: hosted, ref: <its H- id>}`, written by `cwb
    pull`) as applied. The `applied` block is the repo record's (for a
    non-terminal record such as `defer`, which the repo keeps pending, just
    today's date), plus `repo_id` (its DEC- id) and `commit`. Runs before the
    staleness pass, so a decision that changed its own subject is never
    marked stale. `store` is bound to the caller's transaction. Returns the
    H- ids marked."""
    landed = landed_refs(bundle.get("repo_decisions") or [])
    if not landed:
        return []
    stamps = {}
    for r in store.records():
        repo = landed.get(r["id"])
        if repo is None or r.get("status") != "pending":
            continue
        block = dict(repo.get("applied") or {}) if repo.get("status") == "applied" else {}
        block.setdefault("on", datetime.date.today().isoformat())
        block.update(repo_id=repo["id"], commit=commit_sha)
        stamps[r["id"]] = block
    if stamps:
        store.mark_applied(stamps)
    return sorted(stamps)


def publish_snapshot(conn, project_id, bundle, commit_sha):
    """Store and publish one snapshot in the caller's transaction (`conn`).
    The project needs a `cw_repos` row. Reuses the row when the commit already
    has one. Returns the snapshot id."""
    check_format(bundle)
    conn.execute(select(cw_repos.c.project_id).where(cw_repos.c.project_id == project_id)
                 .with_for_update()).scalar_one()
    sid = find_snapshot(conn, project_id, commit_sha)
    if sid is None:
        data, digest = encode_bundle(bundle)
        sid = conn.execute(cw_snapshots.insert().values(
            project_id=project_id, commit_sha=commit_sha,
            schema_fingerprint=bundle["schema_fingerprint"], bundle=data, bundle_sha256=digest,
            warnings=list(bundle.get("warnings") or [])).returning(cw_snapshots.c.id)).scalar_one()
    store = PgStore(conn.engine, project_id)
    with store.using(conn):
        mark_landed(store, bundle, commit_sha)
        stale.run_pass(store, conn, project_id, sid, bundle)
    conn.execute(update(cw_repos).where(cw_repos.c.project_id == project_id).values(
        current_snapshot_id=sid, last_error=None, updated_at=func.now()))
    return sid


def refresh_stale(conn, project_id):
    """The staleness pass against the project's current snapshot, in the
    caller's transaction. A no-op before the first snapshot."""
    sid = conn.execute(select(cw_repos.c.current_snapshot_id)
                       .where(cw_repos.c.project_id == project_id)).scalar_one_or_none()
    if sid is None:
        return None
    store = PgStore(conn.engine, project_id)
    with store.using(conn):
        return stale.run_pass(store, conn, project_id, sid, load_bundle(conn, sid))


__all__ = ["clear_cache", "decode_bundle", "encode_bundle", "find_snapshot", "load_bundle",
           "mark_landed", "publish_snapshot", "refresh_stale"]
