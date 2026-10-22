"""Shared fixtures.

ONGA tests run against a real ONGA checkout, configured by its own
workbench.yaml. Set ONGA_ROOT to an ONGA tree (default: ../onga next to this
repo). Tests that write never touch it: they copy what they need into pytest's
tmp_path, or use ONGA_SCRATCH (a disposable ONGA worktree) for whole-tree runs
such as apply.
"""
import os
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


def onga_root():
    return Path(os.environ.get("ONGA_ROOT", REPO.parent / "onga")).resolve()


@pytest.fixture(scope="session")
def onga():
    root = onga_root()
    if not (root / "workbench.yaml").exists():
        pytest.skip(f"no ONGA tree with a workbench.yaml at {root} (set ONGA_ROOT)")
    return root


@pytest.fixture(scope="session")
def scratch():
    path = os.environ.get("ONGA_SCRATCH")
    if not path or not Path(path, "workbench.yaml").exists():
        pytest.skip("set ONGA_SCRATCH to a disposable ONGA worktree")
    return Path(path).resolve()


def strip_generated(text):
    return "\n".join(ln for ln in text.splitlines() if '"_generated_by"' not in ln)


# ---------------------------------------------------------------- postgres tier
#
# `pg_engine` gives each test a fresh schema in the database named by
# CWB_TEST_POSTGRES_URL (scripts/test-postgres.sh starts a throwaway container),
# with migrations applied by the real runner. Skips when the URL is unset.

TEST_PG_ENV = "CWB_TEST_POSTGRES_URL"


@pytest.fixture(scope="session")
def pg_url():
    url = os.environ.get(TEST_PG_ENV)
    if not url:
        pytest.skip(f"set {TEST_PG_ENV} (scripts/test-postgres.sh) for postgres tests")
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("psycopg")
    return url


@pytest.fixture
def pg_engine(pg_url):
    import uuid

    from sqlalchemy import text

    from curation_workbench.store.pg import make_engine, migrate

    schema = f"t_{uuid.uuid4().hex[:12]}"
    admin = make_engine(pg_url)
    with admin.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    eng = make_engine(pg_url, connect_args={"options": f"-csearch_path={schema}"})
    eng.info_schema = schema
    try:
        migrate.migrate(eng)
        yield eng
    finally:
        eng.dispose()
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def seed_users(engine, *ids):
    """Snapgate accounts with these ids: votes and hosted decisions point at
    `snapgate_users` (migration 022), so store tests that vote as user 1, 2, ...
    create those accounts first."""
    from sqlalchemy import text
    with engine.begin() as conn:
        for i in ids:
            conn.execute(text("INSERT INTO snapgate_users (id, google_id, email, name, status) "
                              "VALUES (:i, :g, :e, :n, 'APPROVED') ON CONFLICT (id) DO NOTHING"),
                         {"i": i, "g": f"seed-{i}", "e": f"user{i}@example.org", "n": f"User {i}"})
        conn.execute(text("SELECT setval(pg_get_serial_sequence('snapgate_users', 'id'), "
                          "(SELECT max(id) FROM snapgate_users))"))
