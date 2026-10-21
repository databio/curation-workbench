"""`cwb hosted worker | resident ... | isolation-check` (plan 4's hosted commands).

Run inside the API or worker container with /run/secrets/curate_env sourced
(Pigeon style); the resident commands act as the superadmin operator. The
project row comes first: `cwb db project add <slug> --name ...`.

This module is imported by `cwb`'s argument parser on a base install too, so it
imports SQLAlchemy and the rest only inside the command functions.
"""
from __future__ import annotations

import json
import sys
import time


def _engine():
    try:
        from ...store.pg import database_url_from_env, make_engine
    except ImportError as e:
        sys.exit(f"cwb hosted needs the hosted extra ({e.name} is missing)")
    try:
        return make_engine(database_url_from_env())
    except RuntimeError as e:
        sys.exit(str(e))


def _fail(e):
    from ...store import StoreError
    if isinstance(e, StoreError):
        sys.exit(f"error ({e.status}): {'; '.join(e.errors)}")
    raise e


def _print(obj):
    print(json.dumps(obj, indent=1, default=str))


def cmd_worker(_, args):
    from .worker import main
    return main()


def cmd_isolation_check(_, args):
    from .isolation import run_isolation_check
    from .settings import SyncSettings
    return run_isolation_check(SyncSettings.from_env().runner_url)


def _res():
    from . import residents
    return residents


def cmd_add(_, args):
    r = _res()
    try:
        out = r.add(_engine(), r.OPERATOR, args.project, args.repo, args.branch, args.config,
                    args.plugin_dir, poll_seconds=args.poll_seconds)
    except Exception as e:  # noqa: BLE001
        _fail(e)
    res = out["resident"]
    print(f"resident {args.project} (id {res['id']}): {res['repo_url']} @ {res['branch']}, "
          f"config {res['config_path']}, plugin dir {res['plugin_dir'] or '-'}")
    print(f"webhook secret (shown once): {out['webhook_secret']}")
    print("a sync is queued; approve the plugin with plugin-diff / approve-plugin")
    return 0


def cmd_list(_, args):
    r = _res()
    rows = r.list_residents(_engine(), r.OPERATOR)
    if not rows:
        print("no residents")
    for x in rows:
        state = "enabled" if x["enabled"] else "disabled"
        print(f"{x['slug']:<16} {state:<8} snapshot={x['current_snapshot_id'] or '-'} "
              f"commit={(x['current_commit'] or '-')[:12]} "
              f"error={'-' if not x['last_error'] else x['last_error'][:80]}")
    return 0


def cmd_show(_, args):
    r = _res()
    try:
        _print(r.show(_engine(), r.OPERATOR, args.slug))
    except Exception as e:  # noqa: BLE001
        _fail(e)
    return 0


def cmd_plugin_diff(_, args):
    r = _res()
    try:
        d = r.plugin_diff(_engine(), r.OPERATOR, args.slug)
    except Exception as e:  # noqa: BLE001
        _fail(e)
    print(f"head {d['head']}\nplugin tree at head: {d['head_tree']}\n"
          f"approved tree: {d['approved_tree'] or '(none)'}\n")
    sys.stdout.write(d["diff"] or "(no changes)\n")
    print(f"\napprove with: cwb hosted resident approve-plugin {args.slug} --tree {d['head_tree']}")
    return 0


def cmd_approve(_, args):
    r = _res()
    try:
        r.approve_plugin(_engine(), r.OPERATOR, args.slug, args.tree)
    except Exception as e:  # noqa: BLE001
        _fail(e)
    print(f"approved plugin tree {args.tree} for {args.slug}; a sync is queued")
    return 0


def cmd_revoke(_, args):
    r = _res()
    try:
        r.revoke_plugin(_engine(), r.OPERATOR, args.slug)
    except Exception as e:  # noqa: BLE001
        _fail(e)
    print(f"plugins revoked for {args.slug}; the next sync runs generic findings only")
    return 0


def cmd_sync(_, args):
    from . import jobs
    from .tables import cw_sync_jobs
    r = _res()
    eng = _engine()
    try:
        job_id = r.sync(eng, r.OPERATOR, args.slug)
    except Exception as e:  # noqa: BLE001
        _fail(e)
    if job_id is None:
        from sqlalchemy import select
        with eng.connect() as conn:
            res = r._row(conn, args.slug)
            job_id = conn.execute(select(cw_sync_jobs.c.id).where(
                cw_sync_jobs.c.resident_id == res["id"], cw_sync_jobs.c.kind == "commit",
                cw_sync_jobs.c.status.in_(("queued", "running")))).scalar_one_or_none()
        print(f"a sync is already queued or running (job {job_id})")
    else:
        print(f"sync queued (job {job_id})")
    if not args.wait or job_id is None:
        return 0
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        with eng.connect() as conn:
            j = jobs.get(conn, job_id)
        if j["status"] in ("done", "failed"):
            print(f"job {job_id}: {j['status']}" + (f": {j['error']}" if j["error"] else "")
                  + (f" (snapshot {j['snapshot_id']})" if j["snapshot_id"] else ""))
            return 0 if j["status"] == "done" else 1
        time.sleep(2)
    print(f"job {job_id} did not finish within {args.timeout} s (is the worker running?)")
    return 1


def cmd_update(_, args):
    r = _res()
    enabled = True if args.enable else False if args.disable else None
    try:
        out = r.update_resident(_engine(), r.OPERATOR, args.slug, branch=args.branch,
                                config_path=args.config, plugin_dir=args.plugin_dir,
                                poll_seconds=args.poll_seconds, enabled=enabled)
    except Exception as e:  # noqa: BLE001
        _fail(e)
    _print(out)
    return 0


def cmd_disable(_, args):
    r = _res()
    try:
        r.update_resident(_engine(), r.OPERATOR, args.slug, enabled=False)
    except Exception as e:  # noqa: BLE001
        _fail(e)
    print(f"{args.slug} disabled (snapshots and votes are kept)")
    return 0


def cmd_rotate(_, args):
    r = _res()
    try:
        secret = r.rotate_webhook_secret(_engine(), r.OPERATOR, args.slug)
    except Exception as e:  # noqa: BLE001
        _fail(e)
    print(f"new webhook secret (shown once): {secret}")
    return 0


def cmd_check(_, args):
    """Rebuild the current snapshot's commit in the runner and compare it, and
    the status derived from it, with what is stored. Writes nothing to the database."""
    from .check import check_resident
    return check_resident(_engine(), args.slug)


def add_parser(sub):
    """Add `cwb hosted ...` to the top-level subparsers; returns the hosted
    subparsers so the API plan can add `serve` and `admin`."""
    h = sub.add_parser("hosted", help="hosted mode: worker, residents, isolation check")
    hs = h.add_subparsers(dest="hosted_cmd", required=True)
    hs.add_parser("worker", help="run the sync worker").set_defaults(fn=cmd_worker)
    hs.add_parser("isolation-check", help="prove the runner is isolated").set_defaults(
        fn=cmd_isolation_check)
    res = hs.add_parser("resident", help="resident projects (superadmin operator)")
    rs = res.add_subparsers(dest="resident_cmd", required=True)
    p = rs.add_parser("add")
    p.add_argument("--project", required=True, help="project slug (cwb db project add first)")
    p.add_argument("--repo", required=True, help="https URL of the git repo")
    p.add_argument("--branch", default="master")
    p.add_argument("--config", default="workbench.yaml", help="workbench.yaml path in the repo")
    p.add_argument("--plugin-dir", default=None, help="plugin folder in the repo")
    p.add_argument("--poll-seconds", type=int, default=300)
    p.set_defaults(fn=cmd_add)
    rs.add_parser("list").set_defaults(fn=cmd_list)
    for name, fn in (("show", cmd_show), ("check", cmd_check), ("plugin-diff", cmd_plugin_diff),
                     ("revoke-plugin", cmd_revoke), ("disable", cmd_disable),
                     ("rotate-webhook-secret", cmd_rotate)):
        p = rs.add_parser(name)
        p.add_argument("slug")
        p.set_defaults(fn=fn)
    p = rs.add_parser("approve-plugin")
    p.add_argument("slug")
    p.add_argument("--tree", required=True, help="the plugin tree id plugin-diff printed")
    p.set_defaults(fn=cmd_approve)
    p = rs.add_parser("sync")
    p.add_argument("slug")
    p.add_argument("--wait", action="store_true", help="wait for the job to finish")
    p.add_argument("--timeout", type=int, default=900)
    p.set_defaults(fn=cmd_sync)
    p = rs.add_parser("update")
    p.add_argument("slug")
    p.add_argument("--branch")
    p.add_argument("--config")
    p.add_argument("--plugin-dir")
    p.add_argument("--poll-seconds", type=int)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--enable", action="store_true")
    g.add_argument("--disable", action="store_true")
    p.set_defaults(fn=cmd_update)
    return hs
