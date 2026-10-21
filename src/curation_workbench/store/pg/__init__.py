"""The hosted (Postgres) store. Needs the `postgres` extra (SQLAlchemy + psycopg 3).

  store.pg.db        `make_engine`, `database_url_from_env` (CWB_DATABASE_URL)
  store.pg.migrate   `migrate`, `pending_migrations`, `missing_schema`, `assert_schema_current`
  store.pg.tables    SQLAlchemy Core tables (`METADATA`) mirroring the migrations
  store.pg.projects  `create_project`, `get_project`, `list_projects`, `archive_project`, ...
  store.pg.store     `PgStore(engine, project_id)`
  store.pg.export    `export_yaml` (hosted decisions -> a repo decisions.yaml)
"""
from . import db, export, migrate, projects, tables
from .db import DATABASE_URL_ENV, database_url_from_env, make_engine
from .export import export_yaml
from .store import PgStore

__all__ = ["DATABASE_URL_ENV", "PgStore", "database_url_from_env", "db", "export", "export_yaml",
           "make_engine", "migrate", "projects", "tables"]
