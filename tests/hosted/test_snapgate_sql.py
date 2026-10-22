"""090_snapgate_useradmin.sql is the vendored snapgate/sql/useradmin.sql, unchanged;
001 + 090 cover every snapgate table (the app's schema check passes on a migrated schema)."""
from pathlib import Path

import pytest

pytestmark = pytest.mark.postgres

REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "deployment" / "migrations" / "090_snapgate_useradmin.sql"
VENDORED = REPO / "src" / "snapgate" / "sql" / "useradmin.sql"


def test_090_is_the_vendored_useradmin_sql_below_its_header():
    header, sep, body = MIGRATION.read_text().partition("\n\n")
    assert sep and header.startswith("-- 090_snapgate_useradmin.sql: ")
    assert all(line.startswith("--") for line in header.splitlines())
    assert body == VENDORED.read_text(), (
        "copy src/snapgate/sql/useradmin.sql into 090_snapgate_useradmin.sql below its header")


def test_migrations_cover_snapgate_and_hosted_tables(engine):
    from curation_workbench import cli
    from curation_workbench.hosted.app import schema_metadata
    from curation_workbench.store.pg import migrate
    assert migrate.missing_schema(engine, schema_metadata()) == []
    migrate.assert_schema_current(engine, cli._extra_metadata())
    names = {t.name for m in cli._extra_metadata() for t in m.sorted_tables}
    assert {"snapgate_users", "api_keys", "snapgate_login_events", "project_members",
            "question_resolutions", "cw_repos", "cw_snapshots"} <= names
