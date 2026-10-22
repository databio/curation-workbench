"""The Workbench: one project's config plus everything its plugins registered.

Generic code never names a project. Anything project-specific reaches it
through a hook registered here by a plugin module's `register(wb)`:

    def register(wb):
        wb.legacy_forms.append(fn)          # (tid, enum, label) -> [reference strings]
        wb.findings_sources.append(fn)      # (wb, fingerprint, severity_of) -> (findings, sources)
        wb.signals["name"] = fn             # (ctx) -> {sid: number}; see status.py
        wb.masked_signals["name"] = fn      # same, zeroed where a terminal decision exists
        wb.term_usage.append(fn)            # (wb) -> {term sid: usage count}, feeds `impact`
        wb.lint_rules.append(fn)            # (lint ctx) -> None (calls ctx.add)
        wb.operation_checks.append(fn)      # (verdict, op) -> [error]
        wb.record_checks.append(fn)         # (wb, rec) -> [error]
        wb.handler(HandlerClass)            # apply handler for (kind, verdict)
        wb.protected_names.append(fn)       # (ctx, section) -> {name: reason}
        wb.ref_providers.insert(i, obj)     # label references outside the schema
        wb.mapping_checks.append(fn)        # (op, negate, ctx) -> [error]
        wb.mapping_fill.append(fn)          # (row, op, negate, table, ctx, new)
        wb.handler_check(kind, verdict, fn)  # (handler, rec, ctx) -> [error]
        # review UI hooks, run only at snapshot time; results ride in the bundle
        wb.suggestions.append(fn)           # (wb) -> {finding id: {object, label, source, score, definition}}
        wb.browse_url = fn                  # (wb, subject) -> absolute URL | None
        wb.subject_panels.append(fn)        # (wb) -> {sid: [{title, text?, links?, table?}]}
        wb.review_prompts.append(fn)        # (wb) -> {sid: [{title, text, source, suggested_verdict,
                                            #          suggested_operation, decide_on}]}

While the review UI hooks run, `wb.snapshot_view` holds the decision-free
data they may use: `{subjects, labels, signals}` (signals unmasked).

Plugin modules are imported from `plugin_dir` when the config sets it (only
that folder is on sys.path while they import, so helpers must be imported at
module top level), else from the config file's folder (then sys.path).

Signal and findings hooks are computed once per commit (snapshot.py) and must
not read decisions; a signal's `ctx` has no decision data.

Plugins run only on the project's own machines (`cwb regen` on a laptop or in
the project's CI). The website never loads them: it reads the snapshot bundle
the repo commits.
"""
import importlib
import sys
from pathlib import Path

from .config import Config


class Workbench:
    def __init__(self, cfg):
        self.cfg = cfg
        self.legacy_forms = []
        self.findings_sources = []
        self.signals = {}
        self.masked_signals = {}   # signals zeroed by derive where a terminal decision exists
        self.term_usage = []
        self.lint_rules = []
        self.operation_checks = []
        self.record_checks = []
        self.handlers = {}
        self.protected_names = []
        self.ref_providers = []
        self.mapping_checks = []
        self.mapping_fill = []
        self.handler_checks = {}
        self.regen = []            # (wb, out_dir) -> None, extra generators run by `cwb regen`
        self.suggestions = []      # review UI hooks (snapshot time only; see snapshot.py)
        self.browse_url = None
        self.subject_panels = []
        self.review_prompts = []
        self.snapshot_view = None  # set by snapshot.build while the review UI hooks run
        self.plugins = []
        self.warnings = []         # carried in the snapshot bundle
        from .store import YamlStore
        self.store = YamlStore(cfg.decisions)   # decision store; the hosted app passes a PgStore

    @classmethod
    def open(cls, config_path, out=None, root=None, plugins=True, store=None):
        """Load a project. `root` overrides the config's project tree (paths are
        re-resolved against it); `out` overrides where generated files go;
        `store` replaces the default decision store (the project's decisions.yaml)."""
        cfg = Config.load(config_path, root=root)
        if out is not None:
            cfg.out = Path(out).resolve()
        wb = cls(cfg)
        if store is not None:
            wb.store = store
        from . import builtin
        builtin.register(wb)
        if plugins:
            wb.load_plugins()
        return wb

    def load_plugins(self):
        pdir = self.cfg.plugin_dir
        if pdir is None:
            here = str(self.cfg.path.parent)
            if here not in sys.path:
                sys.path.insert(0, here)
            for name in self.cfg.plugins:
                mod = importlib.import_module(name)
                mod.register(self)
                self.plugins.append(mod)
            return
        sys.path.insert(0, str(pdir))
        try:
            for name in self.cfg.plugins:
                mod = importlib.import_module(name)
                mod.register(self)
                self.plugins.append(mod)
        finally:
            sys.path.remove(str(pdir))

    def handler(self, obj):
        """Register an apply handler instance (keyed by its `kind` and `verdict`);
        a later registration for the same pair replaces the earlier one."""
        self.handlers[(obj.kind, obj.verdict)] = obj
        return obj

    def handler_check(self, kind, verdict, fn):
        self.handler_checks.setdefault((kind, verdict), []).append(fn)

    def option(self, key, default=None):
        return self.cfg.options.get(key, default)
