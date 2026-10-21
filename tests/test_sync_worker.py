"""The sync worker end to end on a throwaway file:// repo and a throwaway
Postgres schema: poll -> commit job -> snapshot -> publish; idempotent
retries; runner failures; plugin approval rules; derive-only on decision
changes; request_derive; start-up."""
import io

import pytest
from sqlalchemy import func, select, text, update
from synctools import edit_description, set_plugin_message

from curation_workbench import derive, store
from curation_workbench.hosted.sync import publish, request_derive, residents
from curation_workbench.hosted.sync.remote import RunnerError
from curation_workbench.hosted.sync.tables import (cw_derived, cw_residents, cw_snapshots,
                                                   cw_sync_jobs)

pytestmark = pytest.mark.postgres
OP = residents.OPERATOR


def add(env):
    return residents.add(env.engine, OP, "toy", env.url, "master", "workbench.yaml", "plugin",
                         settings=env.settings)


def resident(env):
    with env.engine.connect() as conn:
        return dict(conn.execute(select(cw_residents)).one()._mapping)


def current_bundle(env):
    with env.engine.connect() as conn:
        return publish.load_bundle(conn, resident(env)["current_snapshot_id"])


def run_all(worker, limit=10):
    done = []
    for _ in range(limit):
        j = worker.tick()
        if j is None:
            return done
        done.append(j)
    raise AssertionError("queue never drained")


def count(env, table):
    with env.engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


def approve_head(env):
    d = residents.plugin_diff(env.engine, OP, "toy", settings=env.settings)
    assert "toy_plugin.py" in d["diff"] and d["approved_tree"] is None
    residents.approve_plugin(env.engine, OP, "toy", d["head_tree"], settings=env.settings)
    return d["head_tree"]


def test_add_validates_and_hides_secret(sync_env):
    with pytest.raises(store.StoreError) as e:
        residents.add(sync_env.engine, OP, "toy", "https://evil.example/x", "master",
                      settings=sync_env.settings)
    assert e.value.status == 400
    with pytest.raises(store.StoreError) as e:
        residents.add(sync_env.engine, residents.Caller(is_superadmin=False), "toy", sync_env.url,
                      "master", settings=sync_env.settings)
    assert e.value.status == 403
    out = add(sync_env)
    assert len(out["webhook_secret"]) == 64 and "webhook_secret" not in out["resident"]
    listed = residents.list_residents(sync_env.engine, OP)
    assert [r["slug"] for r in listed] == ["toy"] and "webhook_secret" not in listed[0]
    with pytest.raises(store.StoreError) as e:
        add(sync_env)
    assert e.value.status == 409


def test_first_sync_generic_then_approved(sync_env, sync_worker):
    add(sync_env)
    jobs = run_all(sync_worker)
    assert [(j["kind"], j["outcome"]) for j in jobs] == [("commit", "done")]
    b = current_bundle(sync_env)
    assert "plugin not approved; generic findings only" in b["warnings"]
    assert not any(f["id"] == "plug:color" for f in b["findings"]) and b["plugins"] == []
    r = resident(sync_env)
    with sync_env.engine.connect() as conn:
        d = conn.execute(select(cw_derived)).one()
    assert d.snapshot_id == r["current_snapshot_id"] and r["last_error"] is None
    assert d.status == derive.status(b, b["repo_decisions"])

    tree = approve_head(sync_env)
    jobs = run_all(sync_worker)
    assert [j["kind"] for j in jobs] == ["commit", "checks"]
    b = current_bundle(sync_env)
    assert b["plugins"] == ["toy_plugin"] and any(f["id"] == "plug:color" for f in b["findings"])
    with sync_env.engine.connect() as conn:
        snap = conn.execute(select(cw_snapshots).where(
            cw_snapshots.c.id == resident(sync_env)["current_snapshot_id"])).one()
    assert snap.plugin_tree_used == tree == snap.plugin_tree_at_commit
    shown = residents.show(sync_env.engine, OP, "toy", settings=sync_env.settings)
    assert shown["plugin"]["approved_tree"] == tree == shown["plugin"]["tree_at_head"]
    assert shown["snapshot"]["plugins"] == ["toy_plugin"]


def test_same_commit_is_idempotent(sync_env, sync_worker):
    add(sync_env)
    run_all(sync_worker)
    first = resident(sync_env)["current_snapshot_id"]
    calls = len(sync_worker.runner.calls)
    residents.sync(sync_env.engine, OP, "toy")
    assert [j["outcome"] for j in run_all(sync_worker)] == ["done"]
    assert resident(sync_env)["current_snapshot_id"] == first
    assert count(sync_env, cw_snapshots) == 1
    assert len(sync_worker.runner.calls) == calls          # no second runner call


def test_runner_failure_keeps_old_snapshot(sync_env, sync_worker):
    add(sync_env)
    run_all(sync_worker)
    good = resident(sync_env)["current_snapshot_id"]
    edit_description(sync_env.origin, "meaning: toy:TOY_0002", "meaning: toy:NOPE")
    residents.sync(sync_env.engine, OP, "toy")
    jobs = run_all(sync_worker)
    assert [(j["kind"], j["outcome"]) for j in jobs] == [("commit", "failed")]
    r = resident(sync_env)
    assert r["current_snapshot_id"] == good and "registry_error" in r["last_error"]
    with sync_env.engine.connect() as conn:
        assert "registry_error" in conn.execute(select(cw_sync_jobs.c.error).where(
            cw_sync_jobs.c.status == "failed")).scalar_one()
    sync_worker.runner.fail = RunnerError("runner_unreachable", "down")
    edit_description(sync_env.origin, "meaning: toy:NOPE", "meaning: toy:TOY_0002")
    residents.sync(sync_env.engine, OP, "toy")
    assert [j["outcome"] for j in run_all(sync_worker)] == ["failed"]
    assert "runner_unreachable" in resident(sync_env)["last_error"]
    assert resident(sync_env)["current_snapshot_id"] == good


def test_poll_and_plugin_change_runs_approved_tree(sync_env, sync_worker):
    add(sync_env)
    run_all(sync_worker)
    tree = approve_head(sync_env)
    run_all(sync_worker)
    sha = set_plugin_message(sync_env.origin, "NEW CODE")
    assert run_all(sync_worker) == []                    # not due for a poll yet
    with sync_env.engine.begin() as conn:
        conn.execute(update(cw_residents).values(last_checked_at=None))
    jobs = run_all(sync_worker)
    assert jobs[0]["kind"] == "commit" and jobs[0]["trigger"] == "poll"
    assert jobs[0]["commit_sha"] == sha
    b = current_bundle(sync_env)
    assert any(w.startswith(f"plugin code changed at {sha}") and w.endswith(tree)
               for w in b["warnings"])
    msg = next(f["message"] for f in b["findings"] if f["id"] == "plug:color")
    assert msg == "plugin finding"                       # the approved code ran, not head's
    residents.revoke_plugin(sync_env.engine, OP, "toy")
    run_all(sync_worker)
    b = current_bundle(sync_env)
    assert b["plugins"] == [] and "plugin not approved; generic findings only" in b["warnings"]


def test_decision_change_runs_derive_only(sync_env, sync_worker):
    add(sync_env)
    run_all(sync_worker)
    b = current_bundle(sync_env)
    ctx = store.ValidationContext(subjects=store.Subjects(b["subjects"]), verdicts=b["verdicts"])
    sid = "slot:color"
    sync_env.store.create({"subject": sid, "verdict": "keep", "rationale": "ok", "decided_by": "a",
                           "subject_hashes": {sid: b["subjects"]["subjects"][sid]["hash"]}}, ctx)
    calls = len(sync_worker.runner.calls)
    jobs = run_all(sync_worker)
    assert [j["kind"] for j in jobs] == ["derive"] and len(sync_worker.runner.calls) == calls
    with sync_env.engine.connect() as conn:
        d = conn.execute(select(cw_derived)).one()
    assert d.decisions_version == sync_env.store.decisions_rev()
    assert d.status["subjects"][sid]["decisions"] == ["H-0001"]
    assert d.status["subjects"][sid]["state"] == "settled"


def test_request_derive_enqueues_one_job(sync_env, sync_worker):
    pid = sync_env.project["id"]
    with sync_env.engine.begin() as conn:
        assert request_derive(conn, pid) is None             # no resident yet
    add(sync_env)
    run_all(sync_worker)
    with sync_env.engine.begin() as conn:
        first = request_derive(conn, pid)
        assert first is not None and request_derive(conn, pid) is None
    with sync_env.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(cw_sync_jobs).where(
            cw_sync_jobs.c.kind == "derive", cw_sync_jobs.c.status == "queued")).scalar_one() == 1


def test_checks_job_reports_plugin_errors(sync_env, sync_worker):
    add(sync_env)
    run_all(sync_worker)
    approve_head(sync_env)
    run_all(sync_worker)
    b = current_bundle(sync_env)
    ctx = store.ValidationContext(subjects=store.Subjects(b["subjects"]), verdicts=b["verdicts"])
    sid = "term:TOY_0002"
    sync_env.store.create({"subject": sid, "verdict": "keep", "rationale": "ok", "decided_by": "a",
                           "subject_hashes": {sid: b["subjects"]["subjects"][sid]["hash"]}}, ctx)
    jobs = run_all(sync_worker)
    assert sorted(j["kind"] for j in jobs) == ["checks", "derive"]
    with sync_env.engine.connect() as conn:
        d = conn.execute(select(cw_derived)).one()
    assert d.decision_checks == {"H-0001": ["the toy plugin refuses decisions about blue"]}
    assert d.checks_decisions_version == sync_env.store.decisions_rev()


def test_worker_waits_for_schema_and_announces(sync_env, pg_url, monkeypatch):
    import uuid

    from curation_workbench.hosted.sync import worker as wmod
    from curation_workbench.hosted.sync.settings import SyncSettings
    from curation_workbench.store.pg import make_engine, migrate

    schema = f"t_{uuid.uuid4().hex[:12]}"
    admin = make_engine(pg_url)
    with admin.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    eng = make_engine(pg_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        w = wmod.Worker(eng, sync_env.settings)
        sleeps = []
        with pytest.raises(migrate.SchemaDriftError):
            w.wait_for_schema(sleep=sleeps.append, tries=2)
        assert sleeps == [wmod.SCHEMA_RETRY_SECONDS]
        migrate.migrate(eng)
        monkeypatch.setattr(wmod.Worker, "run_forever", lambda self, stop=None: None)
        buf = io.StringIO()
        assert wmod.main(sync_env.settings, eng, out=buf) == 0
        lines = buf.getvalue().splitlines()
        assert len(lines) == 1 and lines[0].startswith("cwb worker started")
        with pytest.raises(SystemExit, match="CWB_RUNNER_URL"):
            wmod.main(SyncSettings(env="production"), eng, out=buf)
    finally:
        eng.dispose()
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def test_dev_resident(sync_env):
    with sync_env.engine.begin() as conn:
        rid = residents.ensure_dev_resident(conn, sync_env.project["id"], "/tmp/x",
                                            settings=sync_env.settings)
        assert residents.ensure_dev_resident(conn, sync_env.project["id"], "/tmp/y",
                                             settings=sync_env.settings) == rid
    r = resident(sync_env)
    assert r["repo_url"] == "dev:/tmp/y" and r["enabled"] is False
    from curation_workbench.hosted.sync.settings import SyncSettings
    with sync_env.engine.begin() as conn, pytest.raises(store.StoreError):
        residents.ensure_dev_resident(conn, sync_env.project["id"], "/x",
                                      settings=SyncSettings(env="production"))


def test_resident_check_matches(sync_env, sync_worker):
    from curation_workbench.hosted.sync.check import check_resident
    add(sync_env)
    run_all(sync_worker)
    approve_head(sync_env)
    run_all(sync_worker)
    lines = []
    assert check_resident(sync_env.engine, "toy", settings=sync_env.settings,
                          runner=sync_worker.runner.inner, out=lines.append) == 0, lines
    assert lines[-1] == "MATCH"
