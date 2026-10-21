"""Postgres behavior of the sync tables: the SKIP LOCKED claim, the one-live-
job index, stuck recovery, the drift check, and the publish transaction."""
import copy
import shutil
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text

from curation_workbench import cli, snapshot
from curation_workbench.hosted.sync import jobs, publish, residents
from curation_workbench.hosted.sync.tables import (METADATA, cw_derived, cw_residents, cw_snapshots,
                                                   cw_sync_jobs)
from curation_workbench.store.pg import migrate, projects
from curation_workbench.workbench import Workbench

pytestmark = pytest.mark.postgres
TOY = Path(__file__).parent / "fixtures" / "toy"


@pytest.fixture
def rid(pg_engine):
    pid = projects.create_project(pg_engine, "toy", "Toy")["id"]
    with pg_engine.begin() as conn:
        return residents.ensure_dev_resident(conn, pid, "/tmp/toy")


def test_drift_check_covers_sync_tables(pg_engine):
    assert METADATA in cli._extra_metadata()
    migrate.assert_schema_current(pg_engine, cli._extra_metadata())
    with pg_engine.begin() as conn:
        conn.execute(text("ALTER TABLE cw_derived DROP COLUMN review"))
    assert migrate.missing_schema(pg_engine, cli._extra_metadata()) == ["cw_derived.review"]


def test_one_live_job_per_kind(pg_engine, rid):
    with pg_engine.begin() as conn:
        a = jobs.enqueue(conn, rid, "derive", "decisions")
        assert jobs.enqueue(conn, rid, "derive", "manual") is None
        b = jobs.enqueue(conn, rid, "commit", "poll")
    assert a and b
    job = jobs.claim_next(pg_engine)
    assert job["kind"] == "commit" and job["attempt"] == 1          # commit before derive
    with pg_engine.begin() as conn:
        assert jobs.enqueue(conn, rid, "commit", "poll") is None     # running counts as live
    assert jobs.finish(pg_engine, job, "done")
    assert not jobs.finish(pg_engine, job, "failed")                 # no longer owned
    with pg_engine.begin() as conn:
        assert jobs.enqueue(conn, rid, "commit", "poll") is not None


def test_skip_locked_claim(pg_engine, rid):
    with pg_engine.begin() as conn:
        jobs.enqueue(conn, rid, "derive", "decisions")
        jobs.enqueue(conn, rid, "checks", "decisions")
    with pg_engine.connect() as holder:
        trans = holder.begin()
        held = holder.execute(select(cw_sync_jobs.c.id).where(cw_sync_jobs.c.kind == "checks")
                              .with_for_update()).scalar_one()
        got = jobs.claim_next(pg_engine)                 # skips the locked row, takes the next
        assert got["kind"] == "derive" and got["id"] != held
        assert jobs.claim_next(pg_engine) is None
        trans.rollback()
    assert jobs.claim_next(pg_engine)["kind"] == "checks"


def test_recover_stuck(pg_engine, rid):
    with pg_engine.begin() as conn:
        jobs.enqueue(conn, rid, "derive", "decisions")
    now = jobs.utcnow()
    job = jobs.claim_next(pg_engine, now - timedelta(hours=1))
    assert jobs.recover_stuck(pg_engine, stuck_seconds=900, max_attempts=2, now=now) == (1, 0)
    job2 = jobs.claim_next(pg_engine, now - timedelta(hours=1))
    assert job2["id"] == job["id"] and job2["attempt"] == 2
    assert jobs.recover_stuck(pg_engine, stuck_seconds=900, max_attempts=2, now=now) == (0, 1)
    with pg_engine.connect() as conn:
        row = conn.execute(select(cw_sync_jobs)).one()
    assert row.status == "failed" and "2 attempts" in row.error


def test_publish_is_one_transaction(pg_engine, rid, tmp_path, monkeypatch):
    root = tmp_path / "toy"
    shutil.copytree(TOY, root)
    bundle = snapshot.build(Workbench.open(root / "workbench.yaml"))
    with pg_engine.begin() as conn:
        first = publish.publish_snapshot(conn, rid, bundle, "c1")
    b2 = copy.deepcopy(bundle)
    b2["schema_fingerprint"] = "other"

    def boom(*a, **k):
        raise RuntimeError("derive failed")
    monkeypatch.setattr(publish, "write_derived", boom)
    with pytest.raises(RuntimeError), pg_engine.begin() as conn:
        publish.publish_snapshot(conn, rid, b2, "c2")
    with pg_engine.connect() as conn:
        assert conn.execute(select(cw_residents.c.current_snapshot_id)).scalar_one() == first
        assert conn.execute(select(cw_snapshots.c.commit_sha)).scalars().all() == ["c1"]
        assert conn.execute(select(cw_derived.c.snapshot_id)).scalar_one() == first
    with pytest.raises(ValueError):
        with pg_engine.begin() as conn:
            publish.publish_snapshot(conn, rid, {"format": "nope"}, "c3")
