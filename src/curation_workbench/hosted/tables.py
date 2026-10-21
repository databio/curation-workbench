"""SQLAlchemy Core tables mirroring deployment/migrations/020-021.

Registered with the drift guard (`cli._extra_metadata()`, the app's lifespan
check). Foreign keys live in the SQL files only (the referenced tables are on
other MetaData objects); nothing calls `create_all` on this metadata.
"""
from sqlalchemy import BigInteger, Column, Integer, MetaData, Table, Text, func
from sqlalchemy.dialects.postgresql import TIMESTAMP

METADATA = MetaData()

project_members = Table(
    "project_members", METADATA,
    Column("project_id", Integer, primary_key=True),
    Column("user_id", Integer, primary_key=True),
    Column("role", Text, nullable=False),
    Column("added_by", Integer),
    Column("added_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column("removed_at", TIMESTAMP(timezone=True)),
)

question_resolutions = Table(
    "question_resolutions", METADATA,
    Column("id", BigInteger, primary_key=True),
    Column("project_id", Integer, nullable=False),
    Column("subject", Text, nullable=False),
    Column("review_type", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("outcome", Text),
    Column("decision_id", BigInteger),
    Column("resolved_by", Integer),
    Column("note", Text, nullable=False, server_default=""),
    Column("at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
)

__all__ = ["METADATA", "project_members", "question_resolutions"]
