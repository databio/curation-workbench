"""Reproduction tests against a real ONGA checkout.

Set ONGA_ROOT to an ONGA tree (default: ../onga next to this repo). Tests that
write never touch it: they copy what they need into pytest's tmp_path, or use
ONGA_SCRATCH (a disposable ONGA worktree) for whole-tree runs such as apply.
Comparisons ignore only `_generated_by` strings.
"""
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ADAPTER = REPO / "adapters" / "onga" / "workbench.yaml"


def onga_root():
    return Path(os.environ.get("ONGA_ROOT", REPO.parent / "onga")).resolve()


@pytest.fixture(scope="session")
def onga():
    root = onga_root()
    if not (root / "src" / "onga.yaml").exists():
        pytest.skip(f"no ONGA tree at {root} (set ONGA_ROOT)")
    return root


@pytest.fixture(scope="session")
def scratch():
    path = os.environ.get("ONGA_SCRATCH")
    if not path or not Path(path, "src", "onga.yaml").exists():
        pytest.skip("set ONGA_SCRATCH to a disposable ONGA worktree")
    return Path(path).resolve()


def legacy(root, name):
    """Import ONGA's own module `scripts/<name>` (e.g. 'workbench.store') from `root`."""
    scripts = str(root / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module(name)


def same_json(a_text, b_text):
    a, b = json.loads(a_text), json.loads(b_text)
    for d in (a, b):
        if isinstance(d, dict):
            d.pop("_generated_by", None)
    return a == b


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
