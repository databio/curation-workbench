"""The sync queue on `cw_sync_jobs` (Pigeon's jobs.py pattern).

Kinds: `commit` (fetch, snapshot in the runner, publish), `checks` (plugin
decision checks in the runner), `derive` (status/review from the current
bundle and decisions; no runner). Triggers: poll, webhook, manual, decisions.

One live (`queued` or `running`) job per (resident, kind), enforced by the
partial unique index `uq_cw_sync_jobs_live`: a second enqueue is a no-op.
The claim identity of a running job is `(id, attempt)`; stuck recovery
requeues it, so a worker checks it still owns a job before finishing it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .tables import cw_sync_jobs as J

KINDS = ("commit", "derive", "checks")
TRIGGERS = ("poll", "webhook", "manual", "decisions")
LIVE = ("queued", "running")
ERROR_MAX = 4000
#: Claim order: commit before checks before derive, then oldest.
PRIORITY = case({"commit": 0, "checks": 1, "derive": 2}, value=J.c.kind, else_=9)


def utcnow():
    return datetime.now(timezone.utc)


def cut(error):
    error = error or "failed"
    return error if len(error) <= ERROR_MAX else error[: ERROR_MAX - 3] + "..."


def enqueue(conn, resident_id, kind, trigger, commit_sha=None):
    """Queue a job; returns its id, or None when one is already live."""
    if kind not in KINDS or trigger not in TRIGGERS:
        raise ValueError(f"bad job kind/trigger {kind}/{trigger}")
    stmt = (pg_insert(J).values(resident_id=resident_id, kind=kind, trigger=trigger,
                                commit_sha=commit_sha)
            .on_conflict_do_nothing(index_elements=[J.c.resident_id, J.c.kind],
                                    index_where=J.c.status.in_(LIVE))
            .returning(J.c.id))
    return conn.execute(stmt).scalar_one_or_none()


def claim_next(engine, now=None):
    """Claim the next queued job (`FOR UPDATE SKIP LOCKED`) and commit; a dict or None."""
    now = now or utcnow()
    with engine.begin() as conn:
        row = conn.execute(select(J).where(J.c.status == "queued")
                           .order_by(PRIORITY, J.c.created_at, J.c.id).limit(1)
                           .with_for_update(skip_locked=True)).one_or_none()
        if row is None:
            return None
        row = conn.execute(update(J).where(J.c.id == row.id).values(
            status="running", attempt=J.c.attempt + 1, claimed_at=now, finished_at=None,
            error=None).returning(*J.c)).one()
    return dict(row._mapping)


def recover_stuck(engine, *, stuck_seconds, max_attempts, now=None):
    """Requeue (or, after `max_attempts`, fail) jobs left running too long.
    Returns (requeued, failed)."""
    now = now or utcnow()
    cutoff = now - timedelta(seconds=stuck_seconds)
    requeued = failed = 0
    with engine.begin() as conn:
        rows = conn.execute(select(J.c.id, J.c.attempt).where(
            J.c.status == "running", J.c.claimed_at < cutoff).order_by(J.c.id)
            .with_for_update(skip_locked=True)).all()
        for r in rows:
            if r.attempt < max_attempts:
                conn.execute(update(J).where(J.c.id == r.id).values(status="queued", claimed_at=None))
                requeued += 1
            else:
                conn.execute(update(J).where(J.c.id == r.id).values(
                    status="failed", finished_at=now,
                    error=cut(f"did not finish after {r.attempt} attempts (worker stopped or crashed)")))
                failed += 1
    return requeued, failed


def finish(engine_or_conn, job, status, *, error=None, snapshot_id=None, commit_sha=None, now=None):
    """Mark a claimed job done/failed if this claim still owns it; returns whether it did."""
    values = {"status": status, "finished_at": now or utcnow(),
              "error": cut(error) if error else None}
    if snapshot_id is not None:
        values["snapshot_id"] = snapshot_id
    if commit_sha is not None:
        values["commit_sha"] = commit_sha
    stmt = update(J).where(J.c.id == job["id"], J.c.status == "running",
                           J.c.attempt == job["attempt"]).values(**values)
    if hasattr(engine_or_conn, "begin") and not hasattr(engine_or_conn, "in_transaction"):
        with engine_or_conn.begin() as conn:
            return conn.execute(stmt).rowcount == 1
    return engine_or_conn.execute(stmt).rowcount == 1


def get(conn, job_id):
    row = conn.execute(select(J).where(J.c.id == job_id)).one_or_none()
    return dict(row._mapping) if row else None


def recent(conn, resident_id, limit=10):
    rows = conn.execute(select(J).where(J.c.resident_id == resident_id)
                        .order_by(J.c.id.desc()).limit(limit)).all()
    return [dict(r._mapping) for r in rows]
