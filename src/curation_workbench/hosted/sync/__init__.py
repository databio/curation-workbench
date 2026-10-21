"""Resident projects and the sync worker.

  tables      SQLAlchemy Core tables mirroring 040_residents_sync.sql (`METADATA`)
  settings    `SyncSettings` from CWB_* environment variables
  gitrepo     the per-resident bare mirror (ls-remote, fetch, safe archive)
  remote      `RemoteRunner` (HTTP to `cwb runner`) and `LocalRunner` (child process)
  jobs        the cw_sync_jobs queue (SKIP LOCKED claim, one live job per kind)
  publish     `publish_snapshot` and the derived status/review write
  stale       staleness rules for open votes and pending hosted decisions
  residents   superadmin service functions (add, approve plugin, sync, ...)
  webhook     GitHub push webhook handler
  worker      `cwb hosted worker`
  isolation   `cwb hosted isolation-check`

See dev-docs/sync.md. Importing this package loads nothing heavy; each
module imports SQLAlchemy itself.
"""


def request_derive(conn, project_id):
    """Enqueue a `derive` job (trigger `decisions`) for the project's resident.

    A no-op when one is already live or the project has no resident. `conn`
    is the caller's open connection (plan 2 calls this after each write, in
    the write's transaction). Returns the new job id or None."""
    from sqlalchemy import select

    from .jobs import enqueue
    from .tables import cw_residents
    rid = conn.execute(select(cw_residents.c.id)
                       .where(cw_residents.c.project_id == project_id)).scalar_one_or_none()
    if rid is None:
        return None
    return enqueue(conn, rid, "derive", "decisions")
