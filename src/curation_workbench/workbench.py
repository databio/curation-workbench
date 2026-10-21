"""The Workbench: one project's config plus everything its plugins registered.

Generic code never names a project. Anything project-specific reaches it
through a hook registered here by a plugin module's `register(wb)`:

    def register(wb):
        wb.legacy_forms.append(fn)          # (tid, enum, label) -> [reference strings]
        wb.findings_sources.append(fn)      # (wb, fingerprint, severity_of) -> (findings, sources)
        wb.signals["name"] = fn             # (ctx) -> {sid: number}; see status.py
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
        wb.routes.append((method, prefix, fn))  # daemon route; fn(api, tail)

Plugin modules are imported from the config file's folder (then sys.path).
"""
import importlib
import sys

from .config import Config


class Workbench:
    def __init__(self, cfg):
        self.cfg = cfg
        self.legacy_forms = []
        self.findings_sources = []
        self.signals = {}
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
        self.routes = []           # (method, prefix, fn(api, tail)) extra daemon routes
        self.plugins = []
        from .store import YamlStore
        self.store = YamlStore(cfg.decisions)   # decision store; the hosted app passes a PgStore

    @classmethod
    def open(cls, config_path, out=None, root=None, plugins=True, store=None):
        """Load a project. `root` overrides the config's project tree (paths are
        re-resolved against it); `out` overrides where generated files go;
        `store` replaces the default decision store (the project's decisions.yaml)."""
        cfg = Config.load(config_path, root=root)
        if out is not None:
            from pathlib import Path
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
        here = str(self.cfg.path.parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        for name in self.cfg.plugins:
            mod = importlib.import_module(name)
            mod.register(self)
            self.plugins.append(mod)

    def handler(self, obj):
        """Register an apply handler instance (keyed by its `kind` and `verdict`);
        a later registration for the same pair replaces the earlier one."""
        self.handlers[(obj.kind, obj.verdict)] = obj
        return obj

    def handler_check(self, kind, verdict, fn):
        self.handler_checks.setdefault((kind, verdict), []).append(fn)

    def option(self, key, default=None):
        return self.cfg.options.get(key, default)
