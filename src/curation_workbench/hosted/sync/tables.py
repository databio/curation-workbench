"""SQLAlchemy Core tables mirroring deployment/migrations/041_repo_snapshots.sql.

Registered with the drift guard through `cli._extra_metadata()`; nothing calls
`create_all` on this metadata.
"""
from sqlalchemy import (BigInteger, Column, ForeignKey, Integer, LargeBinary, MetaData, Table, Text,
                        UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

METADATA = MetaData()


def _ts(name, nullable=False):
    return Column(name, TIMESTAMP(timezone=True), nullable=nullable,
                  server_default=None if nullable else func.now())


cw_repos = Table(
    "cw_repos", METADATA,
    Column("project_id", Integer, primary_key=True),
    Column("repo", Text, nullable=False),
    Column("branch", Text, nullable=False),
    Column("current_snapshot_id", BigInteger),
    Column("last_commit", Text),
    _ts("last_checked_at", nullable=True),
    Column("last_error", Text),
    _ts("created_at"),
    Column("created_by", Integer),
    _ts("updated_at"),
)

cw_snapshots = Table(
    "cw_snapshots", METADATA,
    Column("id", BigInteger, primary_key=True),
    Column("project_id", Integer, nullable=False),
    Column("commit_sha", Text, nullable=False),
    Column("schema_fingerprint", Text, nullable=False),
    Column("bundle", LargeBinary, nullable=False),
    Column("bundle_sha256", Text, nullable=False),
    Column("warnings", JSONB, nullable=False),
    _ts("created_at"),
    UniqueConstraint("project_id", "commit_sha", name="uq_cw_snapshots_commit"),
)

cw_stale_events = Table(
    "cw_stale_events", METADATA,
    Column("id", BigInteger, primary_key=True),
    Column("project_id", Integer, nullable=False),
    Column("snapshot_id", BigInteger, ForeignKey("cw_snapshots.id")),
    Column("object_kind", Text, nullable=False),
    Column("object_id", BigInteger, nullable=False),
    Column("action", Text, nullable=False),
    Column("sid", Text),
    Column("reason", Text),
    Column("new_sid", Text),
    _ts("at"),
)

__all__ = ["METADATA", "cw_repos", "cw_snapshots", "cw_stale_events"]
