"""The handler contract for applying decisions.

One handler per (kind, verdict) from verdicts.yaml. The apply engine looks
each pending record up in `wb.handlers`, calls `preflight` on every record
first (all-or-nothing: any reason aborts the run), then `apply` on each, and
commits the staged files only when every handler succeeded.

Generic handlers (LinkML terms, enums, subsets, classes, slots, usages,
modules, and SSSOM rows) are in `content.py` and `schema.py`; `builtin`
registers them, plus a no-op handler for every record-only verdict
(`writes: none`). A project plugin registers its own with `wb.handler(obj)`.

A handler never touches disk. It reads through `ctx.read_yaml` / `read_tsv` /
`read_text` (which see earlier staged writes in the same run) and writes by
mutating what those return (the object stays staged) or through `stage_text`.

Extension points a handler consults (all on `ctx.wb`):
  ref_providers     label references to terms outside the schema (see content.py)
  mapping_checks    (op, negate, ctx) -> [error]      extra SSSOM-row rules
  mapping_fill      (row, op, negate, table, ctx, new) extra SSSOM-row columns
  handler_checks    {(kind, verdict): [fn(handler, rec, ctx) -> [error]]}
  protected_names   (ctx, section) -> {name: reason}  classes/enums a rename,
                                                      move or delete must not touch
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import yamlio as _yamlio
from ..tsv import Table


class ApplyError(Exception):
    """A handler (or the engine) found, while staging, that a record cannot apply."""


@dataclass
class Effects:
    """What one applied decision did. The engine stamps `created` / `retired`
    onto the record's `applied` block and feeds `renamed` to the alias map."""
    files: list = field(default_factory=list)
    created: list = field(default_factory=list)    # SIDs created
    retired: list = field(default_factory=list)    # SIDs retired
    renamed: dict = field(default_factory=dict)    # old SID -> new SID


@dataclass
class Ctx:
    """Everything a handler may use.

    wb        the Workbench (config + plugin hooks)
    root      project root; staged paths are relative to it
    tids      termids.TermIds for the project
    subjects  store.Subjects index over the pre-run registry
    staged    {relative path: yamlio.Doc | tsv.Table | str}, the pending writes
    """
    wb: Any
    root: Path
    tids: Any
    subjects: Any
    yamlio: Any = _yamlio
    staged: dict = field(default_factory=dict)

    @property
    def src(self):
        """The schema dir, relative to root (e.g. `src`)."""
        return self.wb.cfg.rel(self.wb.cfg.schema_dir)

    @property
    def ledger(self):
        return self.wb.cfg.rel(self.wb.cfg.ledger)

    def meaning(self, tid):
        return self.tids.meaning(tid)

    def _key(self, path):
        p = Path(path)
        return str(p.relative_to(self.root) if p.is_absolute() else p)

    def read_yaml(self, path):
        """The staged Doc for `path`, loading (and staging) it on first use."""
        key = self._key(path)
        if key not in self.staged:
            self.staged[key] = self.yamlio.load(self.root / key)
        return self.staged[key]

    def read_tsv(self, path):
        """The staged tsv.Table for `path`, loading (and staging) it on first use."""
        key = self._key(path)
        if key not in self.staged:
            self.staged[key] = Table.parse((self.root / key).read_text())
        return self.staged[key]

    def render(self, key):
        cur = self.staged[key]
        if isinstance(cur, str):
            return cur
        if isinstance(cur, Table):
            return cur.dumps()
        return self.yamlio.dumps(cur)

    def read_text(self, path):
        key = self._key(path)
        if key not in self.staged:
            return (self.root / key).read_text()
        return self.render(key)

    def changed(self):
        """{relative path: new text} for every staged file that differs from disk."""
        out = {}
        for key in sorted(self.staged):
            text = self.render(key)
            p = self.root / key
            if not p.exists() or p.read_text() != text:
                out[key] = text
        return out

    def stage_text(self, path, text):
        self.staged[self._key(path)] = text


class RecordOnly:
    """No-op handler for verdicts that only record a judgment."""

    def __init__(self, kind, verdict):
        self.kind = kind
        self.verdict = verdict

    def preflight(self, rec, ctx):
        return []

    def apply(self, rec, ctx):
        return Effects()


def protected(ctx, section):
    """{name: reason} the project protects in `section` (classes / enums)."""
    out = {}
    for fn in ctx.wb.protected_names:
        out.update(fn(ctx, section) or {})
    return out


def extra_checks(handler, rec, ctx):
    errs = []
    for fn in ctx.wb.handler_checks.get((handler.kind, handler.verdict), []):
        errs += fn(handler, rec, ctx)
    return errs
