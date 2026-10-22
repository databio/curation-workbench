"""`cwb hosted serve | project ... | admin add-member | admin dev-snapshot`.

Run the operator commands inside the API container with
/run/secrets/curate_env sourced; they act as the superadmin operator.

`cwb`'s argument parser imports this module on a base install too, so it imports
FastAPI, SQLAlchemy and snapgate only inside the command functions.
"""
from __future__ import annotations

import json
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


def _fail(label, e):
    from ..store import StoreError
    if isinstance(e, StoreError):
        sys.exit(f"{label}: {'; '.join(e.errors)}")
    if hasattr(e, "code") and hasattr(e, "detail"):   # snapgate ServiceError
        sys.exit(f"{label}: {e.detail} ({e.code})")
    raise e


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
    from .service import projects, roles
    engine = _engine()
    try:
        uid = projects.user_id_for_email(engine, args.email)
        out = projects.set_member(engine, roles.OPERATOR, args.project, uid, args.role)
    except Exception as e:  # noqa: BLE001
        _fail("cwb hosted admin add-member", e)
    m = out["member"]
    print(f"account {m['user_id']} is a {m['role']} on {args.project}")
    return 0


def _line(x):
    state = f"error: {x['last_error'][:100]}" if x["last_error"] else "ok"
    return (f"{x['slug']:<16} {x['repo'] or '-'}@{x['branch'] or '-'} "
            f"commit={(x['commit'] or '-')[:12]} synced={x['synced_at'] or '-'} {state}")


def cmd_project_add(_, args):
    from .service import roles
    from .sync import repos
    try:
        out = repos.register(_engine(), roles.OPERATOR, args.slug, args.name or args.slug,
                             args.repo, args.branch)
    except Exception as e:  # noqa: BLE001
        _fail("cwb hosted project add", e)
    print(_line({"slug": args.slug, **out["repo"]}))
    return 1 if out["repo"]["last_error"] else 0


def cmd_project_list(_, args):
    from .service import roles
    from .sync import repos
    rows = repos.list_repos(_engine(), roles.OPERATOR)
    if not rows:
        print("no projects with a repo")
    for x in rows:
        print(_line(x))
    return 0


def cmd_project_show(_, args):
    from .service import roles
    from .sync import repos
    try:
        print(json.dumps(repos.show(_engine(), roles.OPERATOR, args.slug), indent=1, default=str))
    except Exception as e:  # noqa: BLE001
        _fail("cwb hosted project show", e)
    return 0


def cmd_project_sync(_, args):
    from .sync import repos
    try:
        out = repos.sync_project(_engine(), args.slug, force=args.force)
    except Exception as e:  # noqa: BLE001
        _fail("cwb hosted project sync", e)
    if out["error"]:
        print(f"{args.slug}: fetch failed: {out['error']}")
        return 1
    print(f"{args.slug}: commit {out['commit']}"
          + (" (new snapshot published)" if out["published"] else " (already current)"))
    return 0


def dev_snapshot(engine, slug, bundle, label="local"):
    """Publish a snapshot bundle (from `cwb snapshot` in a local checkout) to
    project `slug` under a `dev:<label>` repo that is never fetched. Dev and
    tests only. Returns (snapshot id, commit label)."""
    import datetime

    from ..snapshot import check_format
    from ..store import StoreError
    from ..store.pg import projects as pg_projects
    from .sync import publish, repos

    check_format(bundle)
    project = pg_projects.get_project(engine, slug)
    if project is None:
        raise StoreError(404, [f"no project {slug} (cwb db project add {slug} --name ...)"])
    commit = "dev-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    with engine.begin() as conn:
        repos.ensure_dev_repo(conn, project["id"], label)
        sid = publish.publish_snapshot(conn, project["id"], bundle, commit)
    return sid, commit


def cmd_dev_snapshot(_, args):
    from ..store import StoreError
    from .config import get_settings
    if get_settings().env not in ("dev", "test"):
        sys.exit("cwb hosted admin dev-snapshot is dev only (set CWB_ENV=dev)")
    from pathlib import Path
    try:
        bundle = json.loads(Path(args.snapshot).read_text())
        sid, commit = dev_snapshot(_engine(), args.project, bundle, Path(args.snapshot).name)
    except (StoreError, ValueError, OSError) as e:
        sys.exit(f"cwb hosted admin dev-snapshot: {'; '.join(e.errors) if isinstance(e, StoreError) else e}")
    print(f"published snapshot {sid} ({commit}) for {args.project}")
    return 0


def add_parser(sub):
    """Add `cwb hosted ...` to the top-level subparsers."""
    h = sub.add_parser("hosted", help="the hosted site: serve, projects, admin")
    hs = h.add_subparsers(dest="hosted_cmd", required=True)
    p = hs.add_parser("serve", help="the hosted API and review UI (uvicorn)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8134)
    p.add_argument("--reload", action="store_true", help="restart on code changes (dev)")
    p.set_defaults(fn=cmd_serve)

    proj = hs.add_parser("project", help="projects and their GitHub repos (superadmin operator)")
    ps = proj.add_subparsers(dest="project_cmd", required=True)
    p = ps.add_parser("add", help="register a project with its public GitHub repo; fetch its snapshot")
    p.add_argument("slug")
    p.add_argument("--name", help="display name (default: the slug)")
    p.add_argument("--repo", required=True, help="owner/name or https://github.com/owner/name")
    p.add_argument("--branch", default="master")
    p.set_defaults(fn=cmd_project_add)
    ps.add_parser("list", help="projects with a repo, their commit and last error").set_defaults(
        fn=cmd_project_list)
    p = ps.add_parser("show", help="one project's repo, snapshot, plugins and sources")
    p.add_argument("slug")
    p.set_defaults(fn=cmd_project_show)
    p = ps.add_parser("sync", help="fetch the project's snapshot from GitHub now")
    p.add_argument("slug")
    p.add_argument("--force", action="store_true", help="re-publish even if the commit is current")
    p.set_defaults(fn=cmd_project_sync)

    adm = hs.add_parser("admin", help="operator commands (normally the admin pages)")
    asub = adm.add_subparsers(dest="admin_cmd", required=True)
    p = asub.add_parser("add-member", help="put an approved account on a project's team")
    p.add_argument("--project", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--role", required=True, choices=["voter", "curator"])
    p.set_defaults(fn=cmd_add_member)
    p = asub.add_parser("dev-snapshot", help="dev only: publish a local snapshot file")
    p.add_argument("--project", required=True)
    p.add_argument("--snapshot", required=True, help="a bundle from `cwb snapshot`")
    p.set_defaults(fn=cmd_dev_snapshot)
    return hs
