"""The sync worker: `cwb hosted worker`.

One replica, one job at a time. It never migrates: at start it waits (every
5 s) for `assert_schema_current`, then prints one line starting `cwb worker
started` and loops every `CWB_WORKER_TICK_SECONDS`:

1. recover stuck jobs (running longer than CWB_WORKER_STUCK_SECONDS -> queued;
   after CWB_WORKER_MAX_ATTEMPTS -> failed);
2. poll: each enabled resident due for a check gets `git ls-remote`; a head
   that differs from the current snapshot's commit queues a `commit` job;
3. decisions changed: a `decisions_rev` that differs from `cw_derived`
   queues `derive`; when the bundle's plugins have decision hooks and the
   checks are behind (and the rev has held still for 5 s), queues `checks`;
4. claim one queued job (commit, then checks, then derive) and run it.

`commit`: fetch, pick the plugin tree (the approved one; a changed or
unapproved tree runs the last approved files or none, with a warning),
reuse an existing snapshot for (commit, tree) or archive the commit and
have the runner build the bundle, then `publish_snapshot`. A failure leaves
the current snapshot live and sets `last_error`.

`derive`: status/review from the current bundle and decisions, plus the
staleness pass; no runner, no plugin code.

`checks`: the plugins' decision checks on pending hosted decisions, in the runner.

In production (`CWB_ENV=production`) it refuses to start without `CWB_RUNNER_URL`.
"""
from __future__ import annotations

import logging
import shutil
import sys
import time
from datetime import timedelta
from pathlib import Path

import yaml
from sqlalchemy import select, update

from ...runner import read_members, write_members
from ...store.pg import PgStore
from ...store.pg.tables import projects
from . import gitrepo, jobs, publish, stale
from .remote import RunnerError, make_runner
from .residents import active_approval
from .settings import SyncSettings, worker_violations
from .tables import METADATA, cw_derived, cw_plugin_approvals, cw_residents, cw_snapshots

logger = logging.getLogger("curation_workbench.worker")
STARTED = "cwb worker started"
SCHEMA_RETRY_SECONDS = 5


class JobFailed(Exception):
    pass


class Worker:
    def __init__(self, engine, settings: SyncSettings | None = None, runner=None):
        self.engine = engine
        self.settings = settings or SyncSettings.from_env()
        self.runner = runner or make_runner(self.settings)
        self._bundles = {}      # snapshot id -> bundle (current snapshots only)
        self._rev_seen = {}     # resident id -> (decisions_rev, first seen at)

    # ------------------------------------------------------------ start-up

    def wait_for_schema(self, sleep=time.sleep, tries=None):
        from ...store.pg.migrate import SchemaDriftError, assert_schema_current
        n = 0
        while True:
            try:
                assert_schema_current(self.engine, extra_metadata=(METADATA,))
                return
            except SchemaDriftError as e:
                n += 1
                if tries is not None and n >= tries:
                    raise
                logger.info("waiting for a current database schema: %s", e)
                sleep(SCHEMA_RETRY_SECONDS)

    # ------------------------------------------------------------ helpers

    def _resident(self, conn, rid):
        row = conn.execute(select(cw_residents).where(cw_residents.c.id == rid)).one_or_none()
        return dict(row._mapping) if row else None

    def bundle(self, conn, snapshot_id):
        if snapshot_id not in self._bundles:
            if len(self._bundles) > 16:
                self._bundles.clear()
            self._bundles[snapshot_id] = publish.load_bundle(conn, snapshot_id)
        return self._bundles[snapshot_id]

    def _tree_dir(self, rid):
        return Path(self.settings.sync_dir) / "trees" / str(rid)

    def _save_tree(self, rid, snapshot_id, members):
        d = self._tree_dir(rid)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{snapshot_id}.tar").write_bytes(write_members(members))

    def _load_tree(self, rid, snapshot_id):
        p = self._tree_dir(rid) / f"{snapshot_id}.tar"
        if not p.is_file():
            return None
        return read_members(p.read_bytes(), max_bytes=self.settings.max_tree_bytes + 2**20)

    # ------------------------------------------------------------ the loop

    def tick(self, now=None):
        """One pass of the loop; returns the job it ran (dict with `outcome`) or None."""
        now = now or jobs.utcnow()
        requeued, failed = jobs.recover_stuck(self.engine, stuck_seconds=self.settings.stuck_seconds,
                                              max_attempts=self.settings.max_attempts, now=now)
        if requeued or failed:
            logger.warning("stuck jobs: %d requeued, %d failed", requeued, failed)
        self.poll(now)
        self.watch_decisions(now)
        job = jobs.claim_next(self.engine, now)
        if job is None:
            return None
        return self.run_job(job)

    def run_forever(self, stop=None):
        while stop is None or not stop.is_set():
            try:
                ran = self.tick()
            except Exception:  # noqa: BLE001 - the loop must survive a bad tick
                logger.exception("worker tick failed")
                ran = None
            if ran is None:
                if stop is not None:
                    stop.wait(self.settings.tick_seconds)
                else:
                    time.sleep(self.settings.tick_seconds)

    def poll(self, now):
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(cw_residents, cw_snapshots.c.commit_sha.label("current_commit"))
                .outerjoin(cw_snapshots, cw_snapshots.c.id == cw_residents.c.current_snapshot_id)
                .where(cw_residents.c.enabled.is_(True),
                       ~cw_residents.c.repo_url.startswith("dev:"))).all()
        for row in rows:
            r = dict(row._mapping)
            last = r["last_checked_at"]
            if last is not None and last + timedelta(seconds=r["poll_seconds"]) > now:
                continue
            try:
                sha = gitrepo.for_resident(self.settings, r).head()
            except gitrepo.GitError as e:
                with self.engine.begin() as conn:
                    conn.execute(update(cw_residents).where(cw_residents.c.id == r["id"]).values(
                        last_error=f"poll: {e.message}"[:4000], last_checked_at=now))
                continue
            with self.engine.begin() as conn:
                if sha != r["current_commit"]:
                    jobs.enqueue(conn, r["id"], "commit", "poll", commit_sha=sha)
                conn.execute(update(cw_residents).where(cw_residents.c.id == r["id"]).values(
                    last_seen_commit=sha, last_checked_at=now))

    def watch_decisions(self, now):
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(cw_residents.c.id, cw_residents.c.repo_url, cw_residents.c.current_snapshot_id,
                       projects.c.decisions_rev, cw_derived.c.decisions_version,
                       cw_derived.c.checks_decisions_version, cw_derived.c.snapshot_id)
                .join(projects, projects.c.id == cw_residents.c.project_id)
                .outerjoin(cw_derived, cw_derived.c.resident_id == cw_residents.c.id)
                .where(cw_residents.c.current_snapshot_id.is_not(None))).all()
            for r in rows:
                rev = int(r.decisions_rev)
                if r.decisions_version is None or int(r.decisions_version) != rev \
                        or r.snapshot_id != r.current_snapshot_id:
                    with self.engine.begin() as w:
                        jobs.enqueue(w, r.id, "derive", "decisions")
                if r.repo_url.startswith("dev:") or r.checks_decisions_version == rev:
                    continue
                if not self.bundle(conn, r.current_snapshot_id).get("decision_hooks"):
                    continue
                seen = self._rev_seen.get(r.id)
                if seen is None or seen[0] != rev:
                    self._rev_seen[r.id] = (rev, now)
                elif (now - seen[1]).total_seconds() >= self.settings.checks_debounce_seconds:
                    with self.engine.begin() as w:
                        jobs.enqueue(w, r.id, "checks", "decisions")

    # ------------------------------------------------------------ jobs

    def run_job(self, job):
        t0 = time.monotonic()
        outcome, sha, snapshot_id = "done", job.get("commit_sha"), None
        with self.engine.connect() as conn:
            r = self._resident(conn, job["resident_id"])
        try:
            if r is None:
                raise JobFailed(f"no resident {job['resident_id']}")
            if job["kind"] == "commit":
                sha, snapshot_id = self.commit_job(job, r)
            elif job["kind"] == "derive":
                snapshot_id = self.derive_job(r)
            else:
                snapshot_id = self.checks_job(r)
            jobs.finish(self.engine, job, "done", snapshot_id=snapshot_id, commit_sha=sha)
        except Exception as e:  # noqa: BLE001 - every failure is recorded on the job
            outcome = "failed"
            msg = e.message if isinstance(e, gitrepo.GitError) else str(e)
            if not isinstance(e, (JobFailed, RunnerError, gitrepo.GitError)):
                logger.exception("job %s failed", job["id"])
                msg = f"{type(e).__name__}: {e}"
            jobs.finish(self.engine, job, "failed", error=msg, commit_sha=sha)
            if r is not None and job["kind"] == "commit":
                with self.engine.begin() as conn:
                    conn.execute(update(cw_residents).where(cw_residents.c.id == r["id"]).values(
                        last_error=f"sync {sha or ''}: {msg}"[:4000]))
        secs = time.monotonic() - t0
        logger.info("job %s resident=%s kind=%s trigger=%s sha=%s outcome=%s seconds=%.2f",
                    job["id"], job["resident_id"], job["kind"], job["trigger"], (sha or "-")[:12],
                    outcome, secs)
        return {**job, "outcome": outcome, "commit_sha": sha, "snapshot_id": snapshot_id}

    def _plugin_choice(self, conn, r, repo, sha):
        """(plugins_enabled, plugin_tree_used, tree_at_commit, overlay, warnings)."""
        warnings = []
        raw = repo.show(sha, r["config_path"])
        if raw is None:
            raise JobFailed(f"config_invalid: no {r['config_path']} at {sha}")
        try:
            listed = (yaml.safe_load(raw) or {}).get("plugins") or []
        except (yaml.YAMLError, AttributeError) as e:
            raise JobFailed(f"config_invalid: {r['config_path']} at {sha}: {e}") from None
        if not r["plugin_dir"] or not listed:
            return False, None, None, None, warnings
        tree = repo.plugin_tree(sha, r["plugin_dir"])
        appr = active_approval(conn, r)
        if appr is None:
            warnings.append("plugin not approved; generic findings only")
            return False, None, tree, None, warnings
        if tree != appr["tree_sha"]:
            warnings.append(f"plugin code changed at {sha} (tree {tree}); "
                            f"still running approved tree {appr['tree_sha']}")
        return True, appr["tree_sha"], tree, bytes(appr["files"]), warnings

    def commit_job(self, job, r):
        if r["repo_url"].startswith("dev:"):
            raise JobFailed("dev residents are not synced from git")
        repo = gitrepo.for_resident(self.settings, r)
        sha = repo.fetch()
        with self.engine.connect() as conn:
            enabled, used, at_commit, overlay, warnings = self._plugin_choice(conn, r, repo, sha)
            existing = publish.find_snapshot(conn, r["id"], sha, used)
            bundle = publish.load_bundle(conn, existing) if existing else None
        members = None
        if bundle is None:
            members = repo.archive(sha, r["plugin_dir"], overlay if enabled else None)
            bundle = self.runner.snapshot(members, r["config_path"], enabled)
            bundle["warnings"] = warnings + list(bundle.get("warnings") or [])
        with self.engine.begin() as conn:
            sid = publish.publish_snapshot(conn, r["id"], bundle, sha, used,
                                           plugin_tree_at_commit=at_commit,
                                           commit_time=repo.commit_time(sha))
        self._bundles[sid] = bundle
        if members is not None:
            self._save_tree(r["id"], sid, members)
        return sha, sid

    def derive_job(self, r):
        cur = r["current_snapshot_id"]
        if cur is None:
            return None
        store = PgStore(self.engine, r["project_id"])
        with store.transaction() as conn:
            now = conn.execute(select(cw_residents.c.current_snapshot_id)
                               .where(cw_residents.c.id == r["id"]).with_for_update()).scalar_one()
            if now != cur:
                return None
            bundle = self.bundle(conn, cur)
            stale.run_pass(store, conn, r["id"], cur, bundle)
            publish.write_derived(conn, store, r["id"], cur, bundle)
        return cur

    def checks_job(self, r):
        cur = r["current_snapshot_id"]
        if cur is None or r["repo_url"].startswith("dev:"):
            return None
        store = PgStore(self.engine, r["project_id"])
        with self.engine.connect() as conn:
            bundle = self.bundle(conn, cur)
            snap = conn.execute(select(cw_snapshots.c.commit_sha, cw_snapshots.c.plugin_tree_used)
                                .where(cw_snapshots.c.id == cur)).one()
            files = None
            if snap.plugin_tree_used:
                files = conn.execute(select(cw_plugin_approvals.c.files).where(
                    cw_plugin_approvals.c.resident_id == r["id"],
                    cw_plugin_approvals.c.tree_sha == snap.plugin_tree_used)).scalar_one_or_none()
        if not bundle.get("decision_hooks") or files is None:
            return cur
        rev = store.decisions_rev()
        pending = [d for d in store.records() if d.get("status") == "pending"]
        members = self._load_tree(r["id"], cur)
        if members is None:
            repo = gitrepo.for_resident(self.settings, r)
            repo.fetch()
            members = repo.archive(snap.commit_sha, r["plugin_dir"], bytes(files))
            self._save_tree(r["id"], cur, members)
        checks = self.runner.checks(members, r["config_path"], True, pending) if pending else {}
        with self.engine.begin() as conn:
            conn.execute(update(cw_derived).where(cw_derived.c.resident_id == r["id"],
                                                  cw_derived.c.snapshot_id == cur)
                         .values(decision_checks=checks, checks_decisions_version=rev))
        return cur


def main(settings=None, engine=None, out=None):
    """`cwb hosted worker`: refuse unsafe production settings, wait for the
    schema, announce, loop."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = settings or SyncSettings.from_env()
    bad = worker_violations(settings)
    if bad:
        sys.exit("refusing to start the worker with CWB_ENV=production:\n  - " + "\n  - ".join(bad))
    if engine is None:
        from ...store.pg import database_url_from_env, make_engine
        engine = make_engine(database_url_from_env())
    w = Worker(engine, settings)
    w.wait_for_schema()
    mode = f"runner {settings.runner_url}" if settings.runner_url else "local child-process runner"
    print(f"{STARTED} ({mode}, sync dir {settings.sync_dir})", file=out or sys.stdout, flush=True)
    w.run_forever()
    return 0


__all__ = ["STARTED", "Worker", "main"]
