"""Publishing a snapshot, and writing the derived status/review.

`publish_snapshot` is one transaction: insert (or reuse) the `cw_snapshots`
row, run the staleness pass, derive status and review from the bundle plus
the merged decision list, write `cw_derived` with the `decisions_rev` read in
the same transaction, and make the snapshot current. A failure leaves the
last good snapshot live. Plan 2's dev stand-in (`cwb hosted admin
dev-snapshot`) calls the same function, so dev and production publish alike.
"""
from __future__ import annotations

import gzip
import hashlib
import json

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ... import derive
from ...snapshot import check_format
from ...store.pg import PgStore
from . import jobs, stale
from .tables import cw_derived, cw_residents, cw_snapshots


def encode_bundle(bundle):
    raw = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode()
    return gzip.compress(raw, mtime=0), hashlib.sha256(raw).hexdigest()


def decode_bundle(data):
    return json.loads(gzip.decompress(data))


def find_snapshot(conn, resident_id, commit_sha, plugin_tree_used):
    q = select(cw_snapshots.c.id).where(cw_snapshots.c.resident_id == resident_id,
                                        cw_snapshots.c.commit_sha == commit_sha)
    q = q.where(cw_snapshots.c.plugin_tree_used.is_(None) if plugin_tree_used is None
                else cw_snapshots.c.plugin_tree_used == plugin_tree_used)
    return conn.execute(q).scalar_one_or_none()


def load_bundle(conn, snapshot_id):
    data = conn.execute(select(cw_snapshots.c.bundle)
                        .where(cw_snapshots.c.id == snapshot_id)).scalar_one()
    return decode_bundle(data)


def _resident(conn, resident_id, lock=False):
    q = select(cw_residents).where(cw_residents.c.id == resident_id)
    if lock:
        q = q.with_for_update()
    row = conn.execute(q).one_or_none()
    if row is None:
        raise LookupError(f"no resident {resident_id}")
    return row


def write_derived(conn, store, resident_id, snapshot_id, bundle, *, new_snapshot=False):
    """Derive status and review from `bundle` and the merged decisions and
    overwrite the resident's `cw_derived` row. `store` is bound to `conn`."""
    hosted = store.records()
    decisions = derive.merge_decisions(bundle.get("repo_decisions") or [], hosted)
    st = derive.status(bundle, decisions)
    rv = derive.review(bundle, st, store.snapshot()["next_id"])
    rev = store.decisions_rev()
    values = {"snapshot_id": snapshot_id, "decisions_version": rev, "status": st, "review": rv,
              "computed_at": func.now()}
    insert_values = {**values, "decision_checks": {}}
    stmt = pg_insert(cw_derived).values(resident_id=resident_id, **insert_values)
    if new_snapshot:
        values = {**values, "decision_checks": {}, "checks_decisions_version": None}
    conn.execute(stmt.on_conflict_do_update(index_elements=[cw_derived.c.resident_id], set_=values))
    return rev


def publish_snapshot(conn, resident_id, bundle, commit_sha, plugin_tree_used=None, *,
                     plugin_tree_at_commit=None, commit_time=None):
    """Store and publish one snapshot in the caller's transaction (`conn`).
    Reuses the row when (resident, commit, plugin tree) already has one.
    Returns the snapshot id."""
    check_format(bundle)
    res = _resident(conn, resident_id, lock=True)
    sid = find_snapshot(conn, resident_id, commit_sha, plugin_tree_used)
    if sid is None:
        data, digest = encode_bundle(bundle)
        sid = conn.execute(cw_snapshots.insert().values(
            resident_id=resident_id, commit_sha=commit_sha, commit_time=commit_time,
            schema_fingerprint=bundle["schema_fingerprint"], plugin_tree_used=plugin_tree_used,
            plugin_tree_at_commit=plugin_tree_at_commit, bundle=data, bundle_sha256=digest,
            warnings=list(bundle.get("warnings") or [])).returning(cw_snapshots.c.id)).scalar_one()
    store = PgStore(conn.engine, res.project_id)
    with store.using(conn):
        stale.run_pass(store, conn, resident_id, sid, bundle)
        write_derived(conn, store, resident_id, sid, bundle,
                      new_snapshot=res.current_snapshot_id != sid)
    conn.execute(update(cw_residents).where(cw_residents.c.id == resident_id).values(
        current_snapshot_id=sid, last_error=None, updated_at=func.now()))
    if bundle.get("decision_hooks") and not str(res.repo_url).startswith("dev:"):
        jobs.enqueue(conn, resident_id, "checks", "manual")
    return sid
