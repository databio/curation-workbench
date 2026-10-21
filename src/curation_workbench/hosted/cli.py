"""`cwb hosted serve | admin add-member | admin dev-snapshot` (plan 2), plus
plan 4's `worker | resident ... | isolation-check` (`hosted.sync.cli`).

`cwb`'s argument parser imports this module in local mode too, so it imports
FastAPI, SQLAlchemy and snapgate only inside the command functions.
"""
from __future__ import annotations

import sys


def _engine():
    try:
        from ..store.pg import database_url_from_env, make_engine
    except ImportError as e:
        sys.exit(f"cwb hosted needs the hosted extra ({e.name} is missing)")
    try:
        return make_engine(database_url_from_env())
    except RuntimeError as e:
        sys.exit(str(e))


def cmd_serve(_, args):
    try:
        import uvicorn
    except ImportError:
        sys.exit("cwb hosted serve needs the hosted extra: uv pip install 'curation-workbench[hosted]'")
    uvicorn.run("curation_workbench.hosted.asgi:app", host=args.host, port=args.port,
                reload=args.reload, proxy_headers=True, forwarded_allow_ips="*",
                workers=1)  # in-process rate limits assume ONE process
    return 0


def cmd_add_member(_, args):
    from ..store import StoreError
    from .service import projects, roles
    engine = _engine()
    try:
        uid = projects.user_id_for_email(engine, args.email)
        out = projects.set_member(engine, roles.OPERATOR, args.project, uid, args.role)
    except StoreError as e:
        sys.exit(f"cwb hosted admin add-member: {'; '.join(e.errors)}")
    except Exception as e:  # snapgate ServiceError: NotFound, AccountNotApproved, Invalid
        if hasattr(e, "code") and hasattr(e, "detail"):
            sys.exit(f"cwb hosted admin add-member: {e.detail} ({e.code})")
        raise
    m = out["member"]
    print(f"{m['name']} <{m['email']}> is a {m['role']} on {args.project}")
    return 0


def dev_snapshot(engine, slug, config, *, now=None):
    """Build the snapshot of a local project in-process (trusted local code, no
    runner) and publish it through plan 4's `publish_snapshot` to a disabled
    `dev:<path>` resident. Returns (snapshot id, commit label)."""
    import datetime
    from pathlib import Path

    from .. import snapshot
    from ..config import Config
    from ..store import StoreError
    from ..store.pg import projects as pg_projects
    from ..workbench import Workbench
    from .sync import publish, residents

    path = Path(config).resolve()
    project = pg_projects.get_project(engine, slug)
    if project is None:
        raise StoreError(404, [f"no project {slug} (cwb db project add {slug} --name ...)"])
    root = Config.load(path).root
    wb = Workbench.open(path, root=root, hosted=True)
    bundle = snapshot.build(wb)
    stamp = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y%m%dT%H%M%S%f")
    commit = f"dev-{stamp}"
    with engine.begin() as conn:
        rid = residents.ensure_dev_resident(conn, project["id"], str(root))
        sid = publish.publish_snapshot(conn, rid, bundle, commit)
    return sid, commit


def cmd_dev_snapshot(_, args):
    from ..store import StoreError
    from .config import get_settings
    if get_settings().env not in ("dev", "test"):
        sys.exit("cwb hosted admin dev-snapshot is dev only (set CWB_ENV=dev)")
    try:
        sid, commit = dev_snapshot(_engine(), args.project, args.config)
    except StoreError as e:
        sys.exit(f"cwb hosted admin dev-snapshot: {'; '.join(e.errors)}")
    print(f"published snapshot {sid} ({commit}) for {args.project}")
    return 0


def add_parser(sub):
    """Add `cwb hosted ...` (plan 4's commands, then serve and admin)."""
    from .sync import cli as sync_cli
    hs = sync_cli.add_parser(sub)
    p = hs.add_parser("serve", help="the hosted API and review UI (uvicorn)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8134)
    p.add_argument("--reload", action="store_true", help="restart on code changes (dev)")
    p.set_defaults(fn=cmd_serve)
    adm = hs.add_parser("admin", help="operator commands (normally the admin API)")
    asub = adm.add_subparsers(dest="admin_cmd", required=True)
    p = asub.add_parser("add-member", help="put an approved account on a project's team")
    p.add_argument("--project", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--role", required=True, choices=["voter", "curator"])
    p.set_defaults(fn=cmd_add_member)
    p = asub.add_parser("dev-snapshot", help="dev only: publish a local project's snapshot")
    p.add_argument("--project", required=True)
    p.add_argument("--config", required=True, help="the project's workbench.yaml")
    p.set_defaults(fn=cmd_dev_snapshot)
    return hs
