"""Schema migrations for the hosted store, plus the live-schema guard.

Adapted from Pigeon's `migrate.py`.

* `migrate` APPLIES the numbered `deployment/migrations/*.sql` files that the
  `schema_migrations` ledger has not recorded yet.
* `pending_migrations` lists files the ledger has not recorded; `unknown_migrations`
  lists ledger rows with no file (a database ahead of, or forked from, this code).
* `missing_schema` REFLECTS the live database and lists every table or column
  the registered tables need but the database lacks (this package's
  `tables.METADATA` plus any `extra_metadata` the caller passes).
* `assert_schema_current` raises when anything is pending, unknown or missing.
  The hosted app calls it at start-up; `cwb db status` is
  the same check from the command line.
* `misowned_objects` lists tables/sequences the connecting role does not own.
  `migrate` refuses to apply anything while that list is non-empty.

The guard never trusts the ledger alone: a column added to a table definition
with no migration file leaves the ledger complete and the schema wrong; only
reflection catches that. There is no `create_all` in production.

Guarantees: one Postgres session advisory lock for the whole pass (two booting
processes never apply the same file); each file and its ledger row commit in
ONE transaction (a failure leaves neither and stops before later files); zero
files, a bad file name, or a duplicate number is an error. Postgres only.

Migration numbers (one directory, `deployment/migrations/`; one runner, this one):

  | Numbers | Owner  | Files |
  |---------|--------|-------|
  | 001     | plan 2 | 001_snapgate.sql (snapgate base tables, frozen copy as in Pigeon) |
  | 010-019 | plan 1 | 010_projects.sql, 011_decisions.sql, 012_votes.sql, 013_decision_votes.sql |
  | 020-029 | plan 2 | 020_project_members.sql, 021_question_resolutions.sql, 022_user_fks.sql |
  | 030-039 | free   | |
  | 040-049 | repos  | 040_residents_sync.sql (superseded), 041_repo_snapshots.sql (cw_repos, cw_snapshots, cw_stale_events, and the FKs from 011/012's stale_since_snapshot_id to cw_snapshots) |
  | 090-099 | plan 2 | 090_snapgate_useradmin.sql (byte copy of snapgate's sql/useradmin.sql) |

The anchor file is `010_projects.sql` (it only proves "right directory").
Before the first production deploy, fix a mistake by editing the owning file
and recreating the dev database; after it, only add files.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from sqlalchemy import MetaData, inspect, text
from sqlalchemy.engine import Engine

from .tables import METADATA

logger = logging.getLogger("curation_workbench.migrate")

MIGRATIONS_DIR_ENV = "CWB_MIGRATIONS_DIR"
#: The anchor file; a directory without it is refused ("wrong directory?").
FIRST_MIGRATION = "010_projects.sql"
#: Session-level advisory lock for the whole pass: 0x43574231 == b"CWB1"
#: (distinct from Pigeon's; services share goat's Postgres server).
ADVISORY_LOCK_KEY = 0x43574231
LEDGER_TABLE = "schema_migrations"
FILENAME_RE = re.compile(r"^\d{3}_[a-z0-9_]+\.sql$")

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT now()
)
"""


class MigrationsNotFound(RuntimeError):
    """The migrations directory is absent, empty, or lacks the anchor file."""


class MigrationNamingError(RuntimeError):
    """A file does not match `NNN_name.sql`, or two files share a number."""


class SchemaDriftError(RuntimeError):
    """The live database lacks a table or column, or the ledger and files disagree."""


class SchemaOwnershipError(RuntimeError):
    """A table or sequence is owned by a role other than the connecting one."""


# -- discovery ----------------------------------------------------------------


def _candidate_dirs() -> list[Path]:
    here = Path(__file__).resolve()
    return [
        here.parents[4] / "deployment" / "migrations",  # repo checkout (src layout) / the image
        here.parent / "migrations",  # packaged as package data
    ]


def migrations_dir() -> Path:
    """Locate the migrations directory; `$CWB_MIGRATIONS_DIR` wins."""
    explicit = os.environ.get(MIGRATIONS_DIR_ENV)
    if explicit:
        path = Path(explicit)
        _validate_dir(path, require_first=True)
        return path
    tried = _candidate_dirs()
    for candidate in tried:
        if (candidate / FIRST_MIGRATION).is_file():
            return candidate
    raise MigrationsNotFound(
        f"could not locate deployment/migrations (no directory holding {FIRST_MIGRATION}). "
        f"Set {MIGRATIONS_DIR_ENV}. Tried: " + ", ".join(str(c) for c in tried))


def _validate_dir(path: Path, *, require_first: bool) -> None:
    if not path.is_dir():
        raise MigrationsNotFound(f"migrations directory does not exist: {path}")
    if not any(p.is_file() for p in path.glob("*.sql")):
        raise MigrationsNotFound(f"no .sql migrations in {path}")
    if require_first and not (path / FIRST_MIGRATION).is_file():
        raise MigrationsNotFound(f"{path} holds .sql files but no {FIRST_MIGRATION}; wrong directory?")


def check_names(files: list[Path]) -> None:
    """Every file is `NNN_name.sql` and no two share `NNN`."""
    bad = [p.name for p in files if not FILENAME_RE.match(p.name)]
    if bad:
        raise MigrationNamingError(
            f"migration file names must match {FILENAME_RE.pattern}: {', '.join(bad)}")
    seen: dict[str, str] = {}
    dupes: list[str] = []
    for p in files:
        num = p.name[:3]
        if num in seen:
            dupes.append(f"{seen[num]} / {p.name}")
        seen[num] = p.name
    if dupes:
        raise MigrationNamingError("two migrations share a number (plans colliding?): " + "; ".join(dupes))


def migration_files(directory: Path | str | None = None) -> list[Path]:
    """The `.sql` files in apply order, validated for naming."""
    if directory is None:
        path = migrations_dir()
    else:
        path = Path(directory)
        _validate_dir(path, require_first=False)
    files = sorted(p for p in path.glob("*.sql") if p.is_file())
    check_names(files)
    return files


# -- runner ---------------------------------------------------------------------


def _require_postgres(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        raise RuntimeError(f"migrations are Postgres-only; got a {engine.dialect.name} engine")


def _execute_script(conn, sql: str) -> None:
    """Run a migration file verbatim through the RAW DBAPI cursor.

    Not `text()` (parses `:name` binds, trips on `::` casts) and not
    `exec_driver_sql` (passes a parameter collection, so `%` would be read as
    a placeholder). With no parameters psycopg 3 sends the whole script at once.
    The cursor comes from the same pooled connection, so it runs inside the
    caller's transaction."""
    cursor = conn.connection.cursor()
    try:
        cursor.execute(sql)
    finally:
        cursor.close()


def _applied(conn) -> set[str]:
    if not inspect(conn).has_table(LEDGER_TABLE):
        return set()
    return {row[0] for row in conn.execute(text(f"SELECT filename FROM {LEDGER_TABLE}"))}


def migrate(engine: Engine, extra_metadata=(), *, directory: Path | str | None = None) -> list[str]:
    """Apply every pending migration in order; return the filenames applied.

    Afterwards the live schema must cover `tables.METADATA` plus every
    `extra_metadata`; a gap raises SchemaDriftError (a table definition with
    no migration that creates it)."""
    _require_postgres(engine)
    files = migration_files(directory)
    applied: list[str] = []

    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY})
        conn.commit()
        try:
            with conn.begin():
                _execute_script(conn, _LEDGER_DDL)
            with conn.begin():
                done = _applied(conn)
            pending = [p for p in files if p.name not in done]
            with conn.begin():
                misowned = _misowned(conn)
            if misowned and pending:
                raise SchemaOwnershipError(ownership_message(misowned))
            if misowned:
                logger.warning(ownership_message(misowned))
            for path in pending:
                logger.info("applying migration %s", path.name)
                trans = conn.begin()
                try:
                    _execute_script(conn, path.read_text())
                    conn.execute(text(f"INSERT INTO {LEDGER_TABLE} (filename) VALUES (:f)"),
                                 {"f": path.name})
                    trans.commit()
                except Exception:
                    trans.rollback()
                    logger.error("migration %s failed; rolled back, nothing recorded, "
                                 "later files not run", path.name)
                    raise
                applied.append(path.name)
        finally:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})
                conn.commit()
            except Exception:  # pragma: no cover - the lock dies with the session anyway
                logger.warning("could not release the migration advisory lock", exc_info=True)

    missing = missing_schema(engine, extra_metadata)
    if missing:
        raise SchemaDriftError(_drift_message(missing=missing))
    return applied


def pending_migrations(engine: Engine, *, directory: Path | str | None = None) -> list[str]:
    """Filenames not yet recorded in the ledger, in apply order. Read-only."""
    _require_postgres(engine)
    files = migration_files(directory)
    with engine.connect() as conn:
        done = _applied(conn)
    return [p.name for p in files if p.name not in done]


def unknown_migrations(engine: Engine, *, directory: Path | str | None = None) -> list[str]:
    """Ledger rows with no matching file in the migrations directory. Read-only."""
    _require_postgres(engine)
    names = {p.name for p in migration_files(directory)}
    with engine.connect() as conn:
        done = _applied(conn)
    return sorted(done - names)


# -- ownership guard ------------------------------------------------------------

_MISOWNED_SQL = """
SELECT c.relname,
       CASE c.relkind WHEN 'S' THEN 'sequence' ELSE 'table' END AS kind,
       pg_get_userbyid(c.relowner) AS owner,
       current_user AS me
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = current_schema()
  AND c.relkind IN ('r', 'p', 'S')
  AND c.relowner <> (SELECT oid FROM pg_roles WHERE rolname = current_user)
ORDER BY kind DESC, c.relname
"""


def _misowned(conn) -> list[dict[str, str]]:
    return [{"name": r[0], "kind": r[1], "owner": r[2], "role": r[3]}
            for r in conn.execute(text(_MISOWNED_SQL))]


def misowned_objects(engine: Engine) -> list[dict[str, str]]:
    """Tables and sequences in the current schema not owned by `current_user`."""
    _require_postgres(engine)
    with engine.connect() as conn:
        return _misowned(conn)


def ownership_message(misowned: list[dict[str, str]]) -> str:
    role = misowned[0]["role"]
    listed = ", ".join(f"{m['name']} (owner {m['owner']})" for m in misowned)
    fixes = " ".join(f"ALTER {m['kind'].upper()} {m['name']} OWNER TO {role};" for m in misowned)
    return (f"these tables/sequences are not owned by the connecting role {role}: {listed}. "
            f"`cwb db migrate` applies nothing until this is fixed. As a superuser: {fixes}")


# -- live-schema guard ----------------------------------------------------------


def _required_tables(extra_metadata=()) -> list:
    metas: list[MetaData] = [METADATA, *extra_metadata]
    out, seen = [], set()
    for meta in metas:
        for table in meta.sorted_tables:
            if table.name not in seen:
                seen.add(table.name)
                out.append(table)
    return out


def missing_schema(engine: Engine, extra_metadata=()) -> list[str]:
    """`table` or `table.column` for every required object absent live. Read-only."""
    insp = inspect(engine)
    present = set(insp.get_table_names())
    missing: list[str] = []
    for table in _required_tables(extra_metadata):
        if table.name not in present:
            missing.append(table.name)
            continue
        live = {c["name"] for c in insp.get_columns(table.name)}
        missing.extend(f"{table.name}.{c.name}" for c in table.columns if c.name not in live)
    return missing


def schema_report(engine: Engine, extra_metadata=(), *, directory=None) -> dict[str, list[str]]:
    """{pending, unknown, missing}: everything `cwb db status` reports."""
    return {"pending": pending_migrations(engine, directory=directory),
            "unknown": unknown_migrations(engine, directory=directory),
            "missing": missing_schema(engine, extra_metadata)}


def _drift_message(pending=(), unknown=(), missing=()) -> str:
    try:
        directory: object = migrations_dir()
    except MigrationsNotFound as exc:
        directory = f"<not found: {exc}>"
    parts = []
    if pending:
        parts.append("pending migrations: " + ", ".join(pending) + " (run `cwb db migrate`)")
    if unknown:
        parts.append("migrations recorded in the database with no file here: " + ", ".join(unknown))
    if missing:
        parts.append("missing schema: " + ", ".join(missing)
                     + " (if no numbered .sql file creates these, write the migration)")
    return (f"the curation database is not current: {'; '.join(parts)}. "
            f"Migrations directory: {directory}.")


def assert_schema_current(engine: Engine, extra_metadata=(), *, directory=None) -> None:
    """Raise SchemaDriftError if anything is pending, unknown or missing. Never mutates."""
    report = schema_report(engine, extra_metadata, directory=directory)
    if any(report.values()):
        raise SchemaDriftError(_drift_message(**report))


__all__ = [
    "ADVISORY_LOCK_KEY", "FILENAME_RE", "FIRST_MIGRATION", "MIGRATIONS_DIR_ENV",
    "MigrationNamingError", "MigrationsNotFound", "SchemaDriftError", "SchemaOwnershipError",
    "assert_schema_current", "check_names", "migrate", "migration_files", "migrations_dir",
    "misowned_objects", "missing_schema", "ownership_message", "pending_migrations",
    "schema_report", "unknown_migrations",
]
