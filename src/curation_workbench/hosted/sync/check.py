"""`cwb hosted resident check <slug>`: is what the service publishes what a
regen at the same commit gives?

It rebuilds the current snapshot's commit (same plugin tree) in the runner,
then compares, ignoring warnings:

1. the stored bundle with the rebuilt one;
2. status and review derived from each with the repo's decisions only (an
   empty hosted decision list), i.e. what `cwb regen` writes at that commit;
3. the stored `cw_derived` status with a fresh derive over the current
   hosted decisions (is the derived row current?).

Writes nothing to the database (it may fetch into the worker's mirror).
"""
from __future__ import annotations

from sqlalchemy import select

from ... import derive
from ...store.pg import PgStore
from . import gitrepo, publish
from .remote import RunnerError, make_runner
from .residents import OPERATOR, _row
from .settings import SyncSettings
from .tables import cw_derived, cw_plugin_approvals, cw_snapshots


def _diff_keys(a, b):
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def check_resident(engine, slug, *, settings=None, runner=None, out=print):
    settings = settings or SyncSettings.from_env()
    runner = runner or make_runner(settings)
    with engine.connect() as conn:
        r = _row(conn, slug)
        if r["current_snapshot_id"] is None:
            out(f"FAIL  {slug} has no current snapshot")
            return 1
        snap = conn.execute(select(cw_snapshots).where(
            cw_snapshots.c.id == r["current_snapshot_id"])).one()
        stored = publish.decode_bundle(snap.bundle)
        files = None
        if snap.plugin_tree_used:
            files = conn.execute(select(cw_plugin_approvals.c.files).where(
                cw_plugin_approvals.c.resident_id == r["id"],
                cw_plugin_approvals.c.tree_sha == snap.plugin_tree_used)).scalar_one()
        derived = conn.execute(select(cw_derived.c.status).where(
            cw_derived.c.resident_id == r["id"])).scalar_one_or_none()
    out(f"{slug}: snapshot {snap.id} at {snap.commit_sha} (plugin tree {snap.plugin_tree_used or '-'})")
    try:
        repo = gitrepo.for_resident(settings, r)
        repo.fetch()
        members = repo.archive(snap.commit_sha, r["plugin_dir"], bytes(files) if files else None)
        fresh = runner.snapshot(members, r["config_path"], files is not None)
    except (gitrepo.GitError, RunnerError) as e:
        out(f"FAIL  rebuild: {e}")
        return 1
    ok = True

    def report(passed, label):
        nonlocal ok
        ok = ok and passed
        out(f"{'PASS' if passed else 'FAIL'}  {label}")

    a = {k: v for k, v in stored.items() if k != "warnings"}
    b = {k: v for k, v in fresh.items() if k != "warnings"}
    report(a == b, "bundle matches a rebuild at the same commit"
           + ("" if a == b else f" (differs in {', '.join(_diff_keys(a, b))})"))
    repo_decs = stored.get("repo_decisions") or []
    st_a, st_b = derive.status(stored, repo_decs), derive.status(fresh, repo_decs)
    report(st_a == st_b, "status.json (repo decisions only) matches a local regen")
    report(derive.review(stored, st_a) == derive.review(fresh, st_b),
           "review.json (repo decisions only) matches a local regen")
    store = PgStore(engine, r["project_id"])
    now = derive.status(stored, derive.merge_decisions(repo_decs, store.records()))
    report(derived == now, "cw_derived status is current for the hosted decisions")
    out("MATCH" if ok else "MISMATCH")
    return 0 if ok else 1


__all__ = ["OPERATOR", "check_resident"]
