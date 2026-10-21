"""`cwb`: the curation-workbench command line.

    cwb -c workbench.yaml subjects [--check]     subject registry, crosswalk, aliases
    cwb -c workbench.yaml lint [--check]         schema lint findings
    cwb -c workbench.yaml status [--check]       state + queue score per subject
    cwb -c workbench.yaml review [--check]       queue items sorted into review tracks/types
    cwb -c workbench.yaml regen [--check]        subjects, lint, status, review, in order
    cwb -c workbench.yaml snapshot [--out F]     the commit-phase bundle as JSON (stdout or F)
    cwb derive --snapshot F --decisions D [--out DIR]   status.json + review.json from a bundle
    cwb -c workbench.yaml apply [--dry-run] [--ids DEC-0001 ...]
    cwb -c workbench.yaml serve [--port 8781]    local decision daemon

    cwb db migrate                               apply pending database migrations
    cwb db status                                pending / unknown migrations, missing schema
    cwb db project add <slug> --name NAME        create a hosted project
    cwb db export --project SLUG --template decisions.yaml -o OUT [--ids H-0001 ...]

    cwb runner [--host H] [--port 8150]          the isolated runner (plugin code runs here only)
    cwb hosted worker                            the sync worker (needs the hosted extra)
    cwb hosted resident add|list|show|...        resident projects (superadmin operator)
    cwb hosted isolation-check                   prove the runner is isolated

Global flags: -c/--config (default ./workbench.yaml), --root (project tree;
overrides the config), --out (where generated files go; default the curation dir).
`cwb db`, `cwb derive`, `cwb runner` and `cwb hosted` need no workbench.yaml;
the database commands read CWB_DATABASE_URL (or POSTGRES_DB/USER/PASSWORD/HOST/PORT)
and need the `postgres` extra.
"""
import argparse
import sys
from pathlib import Path

from .workbench import Workbench


NO_WORKBENCH = ("db", "derive", "runner", "hosted")


def _write(files, check, label):
    stale = [p for p, text in files.items() if not p.exists() or p.read_text() != text]
    if check:
        if stale:
            print(f"{label} check FAILED: stale: {', '.join(str(p) for p in stale)}")
            return 1
        return 0
    for p in stale:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(files[p])
    return 0


def cmd_subjects(wb, args):
    from . import subjects
    try:
        files, counts = subjects.render(wb)
    except subjects.RegistryError as e:
        sys.exit(f"SUBJECT REGISTRY FAILED: {e}")
    line = " ".join(f"{k}={v}" for k, v in counts.items())
    rc = _write(files, args.check, "subjects")
    print(f"subjects: {line}" + (" (up to date)" if args.check and not rc else ""))
    return rc


def cmd_lint(wb, args):
    from . import lint
    text, summary = lint.render(wb)
    rc = _write({wb.cfg.findings_dir / "schema_lint.json": text}, args.check, "lint")
    print(f"schema lint: {summary}" + (" (up to date)" if args.check and not rc else ""))
    return rc


def cmd_status(wb, args):
    from . import status
    text, summary = status.render(wb)
    rc = _write({wb.cfg.output("status.json"): text}, args.check, "status")
    print(summary + (" (up to date)" if args.check and not rc else ""))
    return rc


def cmd_review(wb, args):
    from . import review
    if not wb.cfg.review_types.exists():
        print("review: no review_types.yaml; skipped")
        return 0
    text, summary = review.render(wb)
    rc = _write({wb.cfg.output("review.json"): text}, args.check, "review")
    print(f"review: {summary}" + (" (up to date)" if args.check and not rc else ""))
    return rc


def cmd_regen(wb, args):
    """snapshot.build once, then every generated file from it and the decision store."""
    from . import derive, lint, review, snapshot, status, subjects
    try:
        bundle = snapshot.build(wb)
    except subjects.RegistryError as e:
        sys.exit(f"SUBJECT REGISTRY FAILED: {e}")
    up = " (up to date)"
    files = snapshot.files(wb, bundle)
    lint_path = wb.cfg.findings_dir / snapshot.LINT_FILE
    lint_text = files.pop(lint_path)
    rc = _write(files, args.check, "subjects")
    line = " ".join(f"{k}={v}" for k, v in bundle["subjects"]["counts"].items())
    print(f"subjects: {line}" + (up if args.check and not rc else ""))
    r = _write({lint_path: lint_text}, args.check, "lint")
    print(f"schema lint: {lint.summary(bundle['lint'])}" + (up if args.check and not r else ""))
    rc |= r
    st = derive.status(bundle, wb.store.records())
    r = _write({wb.cfg.output("status.json"): status.dump(st)}, args.check, "status")
    print(status.summary(st) + (up if args.check and not r else ""))
    rc |= r
    rv = derive.review(bundle, st, wb.store.snapshot().get("next_id"))
    if rv is None:
        print("review: no review_types.yaml; skipped")
    else:
        r = _write({wb.cfg.output("review.json"): review.dump(rv)}, args.check, "review")
        print(f"review: {review.summary(rv)}" + (up if args.check and not r else ""))
        rc |= r
    for fn in wb.regen:
        fn(wb, wb.cfg.out)
    return rc


def cmd_snapshot(wb, args):
    from . import snapshot, subjects
    try:
        text = snapshot.dumps(snapshot.build(wb))
    except subjects.RegistryError as e:
        sys.exit(f"SUBJECT REGISTRY FAILED: {e}")
    if args.file:
        Path(args.file).write_text(text)
        print(f"snapshot: wrote {args.file}")
    else:
        sys.stdout.write(text)
    return 0


def _read_bundle(path):
    import gzip
    import json

    from . import snapshot
    raw = Path(path).read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return snapshot.check_format(json.loads(raw))


def _read_decisions(path):
    """A JSON list of decision records, or a decisions.yaml."""
    import json
    if Path(path).suffix in (".yaml", ".yml"):
        from .store import YamlStore
        return YamlStore(Path(path)).records()
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        data = data.get("decisions") or []
    return data


def cmd_derive(_, args):
    from . import derive, review, status
    bundle = _read_bundle(args.snapshot)
    decisions = _read_decisions(args.decisions)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    st = derive.status(bundle, decisions)
    (out / "status.json").write_text(status.dump(st))
    print(status.summary(st))
    next_id = max([int(str(d.get("id", "-0")).rsplit("-", 1)[-1] or 0) for d in decisions] + [0]) + 1
    rv = derive.review(bundle, st, next_id)
    if rv is not None:
        (out / "review.json").write_text(review.dump(rv))
        print(f"review: {review.summary(rv)}")
    return 0


def cmd_runner(_, args):
    from . import runner
    return runner.main(["--host", args.host, "--port", str(args.port)])


def cmd_apply(wb, args):
    from . import apply
    return apply.main(wb, dry_run=args.dry_run, ids=args.ids or None, emit_path=args.emit)


def cmd_serve(wb, args):
    from . import server
    server.serve(wb, host=args.host, port=args.port)
    return 0


# ---------------------------------------------------------------- cwb db

def _pg():
    try:
        from .store import pg
    except ImportError as e:
        sys.exit(f"cwb db needs the postgres extra ({e.name} is missing): "
                 "uv pip install 'curation-workbench[postgres]'")
    return pg


def _engine(pg):
    try:
        return pg.make_engine(pg.database_url_from_env())
    except RuntimeError as e:
        sys.exit(str(e))


def _extra_metadata():
    # Plans 2 and 4 register their table metadata here (loaded lazily, only
    # when the `hosted` extra is installed) so the drift check covers every table.
    out = []
    try:
        from .hosted.sync.tables import METADATA as sync_tables
    except ImportError:
        pass
    else:
        out.append(sync_tables)
    return tuple(out)


def cmd_db_migrate(_, args):
    pg = _pg()
    applied = pg.migrate.migrate(_engine(pg), _extra_metadata())
    print(f"applied {len(applied)} migration(s)" + (": " + ", ".join(applied) if applied else ""))
    return 0


def cmd_db_status(_, args):
    pg = _pg()
    report = pg.migrate.schema_report(_engine(pg), _extra_metadata())
    for key, label in (("pending", "pending migrations"), ("unknown", "unknown migrations (in the "
                       "database, no file here)"), ("missing", "missing tables/columns")):
        print(f"{label}: {', '.join(report[key]) or 'none'}")
    return 1 if any(report.values()) else 0


def cmd_db_project_add(_, args):
    from .store import StoreError
    pg = _pg()
    try:
        proj = pg.projects.create_project(_engine(pg), args.slug, args.name)
    except StoreError as e:
        sys.exit(f"cwb db project add: {e}")
    print(f"project {proj['slug']} (id {proj['id']}): {proj['name']}")
    return 0


def cmd_db_export(_, args):
    from .store import StoreError
    pg = _pg()
    engine = _engine(pg)
    proj = pg.projects.get_project(engine, args.project)
    if proj is None:
        sys.exit(f"cwb db export: no project {args.project}")
    try:
        renumbered = pg.export_yaml(pg.PgStore(engine, proj["id"]), args.template, args.output,
                                    ids=args.ids or None)
    except StoreError as e:
        sys.exit(f"cwb db export: {e}")
    print(f"exported {len(renumbered)} decision(s) to {args.output}"
          + "".join(f"\n  {h} -> {d}" for h, d in renumbered.items()))
    return 0


def _db_parser(sub):
    db = sub.add_parser("db", help="hosted database (needs the postgres extra)")
    dsub = db.add_subparsers(dest="db_cmd", required=True)
    dsub.add_parser("migrate").set_defaults(fn=cmd_db_migrate)
    dsub.add_parser("status").set_defaults(fn=cmd_db_status)
    p = dsub.add_parser("export")
    p.add_argument("--project", required=True, help="project slug")
    p.add_argument("--template", required=True, help="the repo's current decisions.yaml")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--ids", nargs="*", help="only these H- ids (default: every pending one)")
    p.set_defaults(fn=cmd_db_export)
    proj = dsub.add_parser("project")
    psub = proj.add_subparsers(dest="project_cmd", required=True)
    p = psub.add_parser("add")
    p.add_argument("slug")
    p.add_argument("--name", required=True)
    p.set_defaults(fn=cmd_db_project_add)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cwb", description=__doc__.splitlines()[0])
    ap.add_argument("-c", "--config", default="workbench.yaml")
    ap.add_argument("--root", help="project tree (overrides the config's root)")
    ap.add_argument("--out", help="where generated files go (default: the curation dir)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("subjects", cmd_subjects), ("lint", cmd_lint),
                     ("status", cmd_status), ("review", cmd_review), ("regen", cmd_regen)):
        p = sub.add_parser(name)
        p.add_argument("--check", action="store_true", help="write nothing; exit 1 if stale")
        p.set_defaults(fn=fn)
    p = sub.add_parser("apply")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--ids", nargs="*")
    p.add_argument("--emit", metavar="PATH", help="write the staged diff and effects as JSON")
    p.set_defaults(fn=cmd_apply)
    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8781)
    p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("snapshot", help="write the commit-phase bundle as JSON")
    p.add_argument("--out", dest="file", help="write the bundle here instead of stdout")
    p.set_defaults(fn=cmd_snapshot)
    p = sub.add_parser("derive", help="status.json and review.json from a bundle and decisions")
    p.add_argument("--snapshot", required=True, help="bundle JSON (from `cwb snapshot`; may be gzip)")
    p.add_argument("--decisions", required=True,
                   help="JSON list of decision records, or a decisions.yaml")
    p.add_argument("--out", dest="out_dir", default=".", help="folder to write to (default: here)")
    p.set_defaults(fn=cmd_derive)
    p = sub.add_parser("runner", help="the isolated runner (HTTP, one job at a time)")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8150)
    p.set_defaults(fn=cmd_runner)
    _db_parser(sub)
    from .hosted.sync import cli as sync_cli
    sync_cli.add_parser(sub)
    args = ap.parse_args(argv)
    if args.cmd in NO_WORKBENCH:
        return args.fn(None, args) or 0
    wb = Workbench.open(Path(args.config), out=args.out, root=args.root)
    return args.fn(wb, args) or 0


if __name__ == "__main__":
    sys.exit(main())
