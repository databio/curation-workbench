"""The apply engine: turn pending decisions into schema edits, all or nothing.

    result = apply.run(wb, want=None, dry_run=False)

1. Select `pending` records with a terminal verdict (optionally only `want`).
2. Rebuild the registry from the schema + SSSOM files and validate each record
   (`store.validate`); a record whose `subject_hashes` no longer match is
   refused as stale.
3. Order the records: renames, then adds, then everything else, then merges,
   then deletes (projects may rank more verdicts: option `apply_rank`); within
   that, a record that references another record's subject in its `operation`
   runs after it. A cycle aborts.
4. Stage: call each handler's `preflight` against the state staged so far and,
   if it has no objection, its `apply`. Any objection aborts the run with the
   full list; nothing has touched disk yet.
5. Dry run: return the unified diff of every staged file. Stop.
6. Otherwise snapshot every file the run can touch (`apply.snapshot` in
   workbench.yaml), write the staged files, run `apply.after_write` commands,
   rebuild the registry, stamp each record (`status: applied`, `applied.on`,
   `subject_hashes_after`, `created`, `retired`, `renamed`), then run
   `apply.regen` commands (default: `cwb regen` in-process) and `apply.verify`
   commands. Any failure restores the snapshot.
"""
import datetime
import difflib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import store, subjects as registry
from .handlers import ApplyError, Ctx
from .termids import TermIds

RANK = {"rename": 0, "rename_enum": 0, "rename_class": 0, "rename_slot": 0,
        "rename_subset": 0,
        "add_term": 1, "add_slot": 1, "add_rule": 1, "set_slot_usage": 1,
        "merge": 3, "merge_subset": 3,
        "delete": 4, "delete_enum": 4, "delete_class": 4, "delete_slot": 4,
        "delete_subset": 4, "drop_slot": 4, "drop_usage": 4, "drop_rule": 4}


def rank(wb, verdict):
    extra = wb.option("apply_rank", {}) or {}
    return extra.get(verdict, RANK.get(verdict, 2))


@dataclass
class Result:
    ok: bool
    records: list = field(default_factory=list)
    effects: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    diff: dict = field(default_factory=dict)
    log: list = field(default_factory=list)


def fresh_subjects(wb):
    records, _ = registry.build(wb)
    return store.Subjects({"schema_fingerprint": registry.fingerprint(records),
                           "subjects": records})


def order(wb, recs, subjects, verdicts):
    by_subject = {}
    for r in recs:
        by_subject.setdefault(r["subject"], []).append(r["id"])
    deps = {r["id"]: set() for r in recs}
    for r in recs:
        kind = subjects.get(r["subject"])["kind"]
        _, refs = store.check_operation(r["verdict"], verdicts[kind][r["verdict"]],
                                        r.get("operation"), subjects, wb=wb)
        for sid in refs:
            deps[r["id"]] |= {q for q in by_subject.get(sid, []) if q != r["id"]}
    key = {r["id"]: (rank(wb, r["verdict"]), r["id"]) for r in recs}
    done, out = set(), []
    while len(out) < len(recs):
        ready = sorted((rid for rid in deps if rid not in done and deps[rid] <= done),
                       key=key.get)
        if not ready:
            cyc = sorted(rid for rid in deps if rid not in done)
            raise ApplyError(f"decisions depend on each other in a cycle: {', '.join(cyc)}")
        done.add(ready[0])
        out.append(ready[0])
    idx = {r["id"]: r for r in recs}
    return [idx[i] for i in out]


def select(records, verdicts, subjects, want=None):
    recs, errors = [], {}
    known = {r["id"] for r in records}
    for rid in want or ():
        if rid not in known:
            errors[rid] = [f"no decision {rid}"]
    for r in records:
        if want and r["id"] not in want:
            continue
        if r["status"] != "pending":
            if want:
                errors[r["id"]] = [f"{r['id']} is {r['status']}, not pending"]
            continue
        subj = subjects.get(r["subject"])
        kind = subj["kind"] if subj else None
        entry = verdicts.get(kind, {}).get(r["verdict"])
        if entry is not None and not entry["terminal"]:
            continue
        recs.append(r)
    return recs, errors


def validate(wb, recs, subjects, verdicts, known_ids=None):
    errors = {}
    for r in recs:
        rec = dict(r)
        if isinstance(rec.get("decided_on"), str):
            rec["decided_on"] = datetime.date.fromisoformat(rec["decided_on"])
        try:
            store.validate(rec, subjects, verdicts, known_ids=known_ids, check_hashes=False, wb=wb)
        except store.StoreError as e:
            errors[r["id"]] = e.errors
            continue
        stale = [f"{sid} changed since the decision was recorded"
                 for sid, h in (r.get("subject_hashes") or {}).items()
                 if subjects.get(sid) and subjects.get(sid)["hash"] != h]
        if stale:
            errors[r["id"]] = stale
        elif (subjects.get(r["subject"])["kind"], r["verdict"]) not in wb.handlers:
            errors[r["id"]] = [f"no handler for {subjects.get(r['subject'])['kind']} "
                               f"{r['verdict']} yet"]
    return errors


def stage(wb, recs, subjects):
    ctx = Ctx(wb=wb, root=wb.cfg.root, tids=TermIds(wb.cfg), subjects=subjects)
    effects, errors = {}, {}
    for r in recs:
        h = wb.handlers[(subjects.get(r["subject"])["kind"], r["verdict"])]
        problems = h.preflight(r, ctx)
        if problems:
            errors[r["id"]] = problems
            continue
        try:
            effects[r["id"]] = h.apply(r, ctx)
        except ApplyError as e:
            errors[r["id"]] = [str(e)]
    return ctx, effects, errors


def diffs(changed, root):
    out = {}
    for path, text in changed.items():
        p = root / path
        before = p.read_text() if p.exists() else ""
        out[path] = "".join(difflib.unified_diff(
            before.splitlines(True), text.splitlines(True), f"a/{path}", f"b/{path}"))
    return out


def plan(wb, want=None):
    """Everything up to (and including) staging. Returns (Result, ctx or None)."""
    verdicts = store.load_verdicts(wb.cfg.verdicts)
    try:
        subjects = fresh_subjects(wb)
    except registry.RegistryError as e:
        return Result(False, errors={"run": [f"subject registry: {e}"]}), None
    records = wb.store.records()
    recs, errors = select(records, verdicts, subjects, want)
    known = {r["id"] for r in records}
    errors.update(validate(wb, [r for r in recs if r["id"] not in errors], subjects, verdicts, known))
    if errors:
        return Result(False, errors=errors), None
    try:
        recs = order(wb, recs, subjects, verdicts)
    except ApplyError as e:
        return Result(False, errors={"run": [str(e)]}), None
    ctx, effects, errors = stage(wb, recs, subjects)
    if errors:
        return Result(False, records=recs, errors=errors), None
    return Result(True, records=recs, effects=effects, diff=diffs(ctx.changed(), wb.cfg.root)), ctx


# ---------------------------------------------------------------- commit

def apply_conf(wb):
    conf = wb.cfg.data.get("apply") or {}
    cfg = wb.cfg
    default_snap = [cfg.rel(cfg.schema_dir), cfg.rel(cfg.curation)]
    default_snap += sorted({cfg.rel(p.parent) for p in cfg.sssom_files()})
    return {"snapshot": conf.get("snapshot") or default_snap,
            "after_write": conf.get("after_write") or [],
            "regen": conf.get("regen"),
            "verify": conf.get("verify") or []}


def _snapshot(root, tmp, paths):
    for rel in paths:
        src = root / rel
        if src.is_dir():
            shutil.copytree(src, tmp / rel, symlinks=True)
        elif src.exists():
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tmp / rel)


def _restore(root, tmp, paths):
    for rel in paths:
        dst, saved = root / rel, tmp / rel
        if dst.is_dir():
            shutil.rmtree(dst)
        elif dst.exists():
            dst.unlink()
        if saved.is_dir():
            shutil.copytree(saved, dst, symlinks=True)
        elif saved.exists():
            shutil.copy2(saved, dst)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _run(cmd, root, log):
    argv = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
    argv = [sys.executable if a == "{python}" else a for a in argv]
    proc = subprocess.run(argv, cwd=root, capture_output=True, text=True)
    log.append(f"$ {' '.join(map(str, argv))} -> exit {proc.returncode}")
    if proc.returncode:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-40:]
        raise ApplyError(f"`{' '.join(map(str, argv))}` failed:\n" + "\n".join(tail))


def _write_registry(wb):
    files, _ = registry.render(wb)
    for p, text in files.items():
        if not p.exists() or p.read_text() != text:
            _write(p, text)


def _regen_inprocess(wb):
    from . import derive, snapshot, status
    bundle = snapshot.build(wb)
    files = snapshot.files(wb, bundle)
    lint_path = wb.cfg.findings_dir / snapshot.LINT_FILE
    lint_text = files.pop(lint_path)
    for p, text in files.items():
        if not p.exists() or p.read_text() != text:
            _write(p, text)
    _write(lint_path, lint_text)
    _write(wb.cfg.output("status.json"), status.dump(derive.status(bundle, wb.store.records())))
    for fn in wb.regen:
        fn(wb, wb.cfg.out)


def _stamp(wb, result, before):
    """Mark every applied record, with post-apply hashes from the new registry.

    Subjects the run changed as a side effect are stamped on the run's last
    record, so they do not read as hand-edited (`stale`) afterwards."""
    after = json.loads(wb.cfg.output("subjects.json").read_text())["subjects"]
    today = datetime.date.today()
    stamps = {}
    touched = set()
    for i, r in enumerate(result.records):
        eff = result.effects[r["id"]]
        affected = [r["subject"], *(r.get("also_affects") or []), *eff.created]
        affected = [eff.renamed.get(s, s) for s in affected]
        hashes = {s: after[s]["hash"] for s in dict.fromkeys(affected) if s in after}
        touched.update(affected)
        if i == len(result.records) - 1:
            hashes.update({s: a["hash"] for s, a in after.items() if s not in touched
                           and s in before and before[s]["hash"] != a["hash"]})
        stamps[r["id"]] = {
            "on": today,
            "subject_hashes_after": hashes,
            "created": list(eff.created), "retired": list(eff.retired),
            **({"renamed": dict(eff.renamed)} if eff.renamed else {}),
        }
    wb.store.mark_applied(stamps)


def _regen_and_verify(wb, conf, log):
    if conf["regen"] is None:
        _regen_inprocess(wb)
        log.append("regen (in-process) -> ok")
    else:
        for cmd in conf["regen"]:
            _run(cmd, wb.cfg.root, log)
    for cmd in conf["verify"]:
        _run(cmd, wb.cfg.root, log)


def run(wb, want=None, dry_run=False, *, regen_if_empty=False):
    """Apply pending records (or only `want`). `regen_if_empty`: when nothing
    is left to apply (for example only non-terminal records such as `defer`
    were added to decisions.yaml), still run regen and verify, all or nothing,
    so files derived from the decisions stay current."""
    if wb.store.kind != "yaml":
        raise ApplyError("apply works on the repo's decisions.yaml; hosted decisions get there "
                         "through `cwb pull`")
    result, ctx = plan(wb, want)
    if not result.ok or dry_run or (not result.records and not regen_if_empty):
        return result
    root, conf = wb.cfg.root, apply_conf(wb)
    with tempfile.TemporaryDirectory(prefix="cwb-apply-") as tmp:
        tmp = Path(tmp)
        _snapshot(root, tmp, conf["snapshot"])
        try:
            if result.records:
                for path, text in ctx.changed().items():
                    _write(root / path, text)
                for cmd in conf["after_write"]:
                    _run(cmd, root, result.log)
                _write_registry(wb)
                _stamp(wb, result, ctx.subjects.by_sid)
            _regen_and_verify(wb, conf, result.log)
        except (ApplyError, OSError, registry.RegistryError) as e:
            _restore(root, tmp, conf["snapshot"])
            result.ok = False
            result.errors["run"] = [str(e), "all files restored; nothing applied"]
    return result


def emit(result):
    """The staged diff plus each decision's effects, as the Pending page reads it."""
    return {
        "_generated_by": "cwb apply --dry-run --emit; do not edit",
        "ok": result.ok,
        "decisions": [
            {"id": r["id"], "subject": r["subject"], "verdict": r["verdict"],
             **({"created": e.created, "retired": e.retired, "renamed": e.renamed}
                if (e := result.effects.get(r["id"])) else {})}
            for r in result.records],
        "files": [{"path": p, "diff": d} for p, d in sorted(result.diff.items())],
        "errors": result.errors,
    }


def main(wb, dry_run=False, ids=None, emit_path=None):
    result = run(wb, want=ids, dry_run=dry_run)
    if emit_path:
        Path(emit_path).write_text(json.dumps(emit(result), indent=1, ensure_ascii=False) + "\n")
    if not result.ok:
        print(f"APPLY REFUSED ({sum(map(len, result.errors.values()))} problem(s)); nothing applied:")
        for rid, errs in sorted(result.errors.items()):
            for e in errs:
                print(f"  {rid}: {e}")
        return 1
    if dry_run:
        sys.stdout.write("".join(d for _, d in sorted(result.diff.items())))
        return 0
    for line in result.log:
        print(line)
    if result.records:
        print(f"applied {len(result.records)} decision(s): "
              + ", ".join(f"{r['id']} ({r['verdict']})" for r in result.records))
    return 0
