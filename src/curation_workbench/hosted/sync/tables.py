"""SQLAlchemy Core tables mirroring deployment/migrations/040_residents_sync.sql.

Registered with the drift guard through `cli._extra_metadata()`; nothing calls
`create_all` on this metadata.
"""
from sqlalchemy import (BigInteger, Boolean, Column, ForeignKey, Integer, LargeBinary, MetaData,
                        Table, Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSON, JSONB, TIMESTAMP

METADATA = MetaData()


def _ts(name, nullable=False):
    return Column(name, TIMESTAMP(timezone=True), nullable=nullable,
                  server_default=None if nullable else func.now())


cw_residents = Table(
    "cw_residents", METADATA,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, nullable=False, unique=True),
    Column("repo_url", Text, nullable=False),
    Column("branch", Text, nullable=False),
    Column("config_path", Text, nullable=False, server_default="workbench.yaml"),
    Column("plugin_dir", Text),
    Column("enabled", Boolean, nullable=False, server_default="true"),
    Column("poll_seconds", Integer, nullable=False, server_default="300"),
    Column("webhook_secret", Text),
    Column("last_seen_commit", Text),
    Column("current_snapshot_id", BigInteger),
    Column("last_error", Text),
    _ts("last_checked_at", nullable=True),
    _ts("plugins_revoked_at", nullable=True),
    _ts("created_at"),
    Column("created_by", Integer),
    _ts("updated_at"),
)

cw_plugin_approvals = Table(
    "cw_plugin_approvals", METADATA,
    Column("id", Integer, primary_key=True),
    Column("resident_id", Integer, ForeignKey("cw_residents.id"), nullable=False),
    Column("tree_sha", Text, nullable=False),
    Column("files", LargeBinary, nullable=False),
    Column("approved_by", Integer),
    _ts("approved_at"),
    UniqueConstraint("resident_id", "tree_sha", name="uq_cw_plugin_approvals"),
)

cw_snapshots = Table(
    "cw_snapshots", METADATA,
    Column("id", BigInteger, primary_key=True),
    Column("resident_id", Integer, ForeignKey("cw_residents.id"), nullable=False),
    Column("commit_sha", Text, nullable=False),
    _ts("commit_time", nullable=True),
    Column("schema_fingerprint", Text, nullable=False),
    Column("plugin_tree_used", Text),
    Column("plugin_tree_at_commit", Text),
    Column("bundle", LargeBinary, nullable=False),
    Column("bundle_sha256", Text, nullable=False),
    Column("warnings", JSONB, nullable=False),
    _ts("created_at"),
)

cw_derived = Table(
    "cw_derived", METADATA,
    Column("resident_id", Integer, ForeignKey("cw_residents.id"), primary_key=True),
    Column("snapshot_id", BigInteger, ForeignKey("cw_snapshots.id"), nullable=False),
    Column("decisions_version", BigInteger, nullable=False),
    Column("status", JSON(none_as_null=True), nullable=False),
    Column("review", JSON(none_as_null=True)),
    Column("decision_checks", JSONB, nullable=False),
    Column("checks_decisions_version", BigInteger),
    _ts("computed_at"),
)

cw_sync_jobs = Table(
    "cw_sync_jobs", METADATA,
    Column("id", BigInteger, primary_key=True),
    Column("resident_id", Integer, ForeignKey("cw_residents.id"), nullable=False),
    Column("kind", Text, nullable=False),
    Column("trigger", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="queued"),
    Column("attempt", Integer, nullable=False, server_default="0"),
    _ts("created_at"),
    _ts("claimed_at", nullable=True),
    _ts("finished_at", nullable=True),
    Column("error", Text),
    Column("commit_sha", Text),
    Column("snapshot_id", BigInteger, ForeignKey("cw_snapshots.id")),
)

cw_stale_events = Table(
    "cw_stale_events", METADATA,
    Column("id", BigInteger, primary_key=True),
    Column("resident_id", Integer, ForeignKey("cw_residents.id"), nullable=False),
    Column("snapshot_id", BigInteger, ForeignKey("cw_snapshots.id")),
    Column("object_kind", Text, nullable=False),
    Column("object_id", BigInteger, nullable=False),
    Column("action", Text, nullable=False),
    Column("sid", Text),
    Column("reason", Text),
    Column("new_sid", Text),
    _ts("at"),
)

__all__ = ["METADATA", "cw_derived", "cw_plugin_approvals", "cw_residents", "cw_snapshots",
           "cw_stale_events", "cw_sync_jobs"]
