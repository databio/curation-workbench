"""`cwb`: the curation-workbench command line.

    cwb -c workbench.yaml subjects [--check]     subject registry, crosswalk, aliases
    cwb -c workbench.yaml lint [--check]         schema lint findings
    cwb -c workbench.yaml status [--check]       state + queue score per subject
    cwb -c workbench.yaml review [--check]       queue items sorted into review tracks/types
    cwb -c workbench.yaml regen [--check]        subjects, lint, status, review, in order
    cwb -c workbench.yaml apply [--dry-run] [--ids DEC-0001 ...]
    cwb -c workbench.yaml serve [--port 8781]    local decision daemon

Global flags: -c/--config (default ./workbench.yaml), --root (project tree;
overrides the config), --out (where generated files go; default the curation dir).
"""
import argparse
import sys
from pathlib import Path

from .workbench import Workbench


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
    rc = 0
    for fn in (cmd_subjects, cmd_lint, cmd_status, cmd_review):
        rc |= fn(wb, args)
    for fn in wb.regen:
        fn(wb, wb.cfg.out)
    return rc


def cmd_apply(wb, args):
    from . import apply
    return apply.main(wb, dry_run=args.dry_run, ids=args.ids or None, emit_path=args.emit)


def cmd_serve(wb, args):
    from . import server
    server.serve(wb, host=args.host, port=args.port)
    return 0


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
    args = ap.parse_args(argv)
    wb = Workbench.open(Path(args.config), out=args.out, root=args.root)
    return args.fn(wb, args) or 0


if __name__ == "__main__":
    sys.exit(main())
