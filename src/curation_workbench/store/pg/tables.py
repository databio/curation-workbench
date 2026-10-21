"""SQLAlchemy Core tables mirroring deployment/migrations/010-013 exactly.

Used for queries and by the drift guard (`migrate.missing_schema`). The
migration files are the schema; nothing calls `create_all` on this metadata.
"""
from sqlalchemy import (BigInteger, Column, Date, ForeignKey, Integer, MetaData, Table, Text,
                        UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

METADATA = MetaData()


def _ts(name, nullable=False):
    return Column(name, TIMESTAMP(timezone=True), nullable=nullable,
                  server_default=None if nullable else func.now())


projects = Table(
    "projects", METADATA,
    Column("id", Integer, primary_key=True),
    Column("slug", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("next_decision_number", Integer, nullable=False, server_default="1"),
    Column("decisions_rev", BigInteger, nullable=False, server_default="0"),
    _ts("archived_at", nullable=True),
    _ts("created_at"),
    _ts("updated_at"),
)

decisions = Table(
    "decisions", METADATA,
    Column("id", BigInteger, primary_key=True),
    Column("project_id", Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
    Column("dec_id", Text, nullable=False),
    Column("subject", Text, nullable=False),
    Column("also_affects", JSONB, nullable=False),
    Column("verdict", Text, nullable=False),
    Column("operation", JSONB(none_as_null=True)),
    Column("rationale", Text, nullable=False),
    Column("evidence", JSONB, nullable=False),
    Column("decided_by", Text, nullable=False),
    Column("decided_by_user_id", Integer),
    Column("decided_on", Date, nullable=False),
    Column("subject_hashes", JSONB, nullable=False),
    Column("schema_fingerprint", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("applied", JSONB, nullable=False),
    Column("supersedes", Text),
    Column("origin", JSONB, nullable=False),
    Column("extra", JSONB, nullable=False),
    Column("rev", Integer, nullable=False, server_default="1"),
    Column("stale_since_snapshot_id", BigInteger),
    Column("stale_reason", Text),
    Column("stale_new_sid", Text),
    _ts("created_at"),
    _ts("updated_at"),
    UniqueConstraint("project_id", "dec_id", name="uq_decisions_project_dec"),
)

votes = Table(
    "votes", METADATA,
    Column("id", BigInteger, primary_key=True),
    Column("project_id", Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
    Column("user_id", Integer, nullable=False),
    Column("subject", Text, nullable=False),
    Column("review_type", Text, nullable=False),
    Column("answer", Text, nullable=False),
    Column("proposal", JSONB(none_as_null=True)),
    Column("comment", Text, nullable=False),
    Column("subject_hash", Text, nullable=False),
    Column("schema_fingerprint", Text, nullable=False),
    Column("rev", Integer, nullable=False, server_default="1"),
    Column("stale_since_snapshot_id", BigInteger),
    Column("stale_reason", Text),
    Column("stale_new_sid", Text),
    _ts("created_at"),
    _ts("updated_at"),
    UniqueConstraint("project_id", "subject", "review_type", "user_id",
                     name="uq_votes_one_per_person"),
)

decision_votes = Table(
    "decision_votes", METADATA,
    Column("decision_id", BigInteger, ForeignKey("decisions.id", ondelete="CASCADE"),
           primary_key=True),
    Column("vote_id", BigInteger, ForeignKey("votes.id", ondelete="RESTRICT"), primary_key=True),
    Column("answer", Text, nullable=False),
    Column("proposal", JSONB(none_as_null=True)),
    Column("subject_hash", Text, nullable=False),
    Column("vote_rev", Integer, nullable=False),
)

__all__ = ["METADATA", "decision_votes", "decisions", "projects", "votes"]
