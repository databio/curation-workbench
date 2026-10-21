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
        wb.routes.append((method, prefix, fn))  # daemon route; fn(api, tail)
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

Hosted mode (`open(..., hosted=True)`, used only by the isolated runner): the
project tree is the checkout, every config path must stay inside it,
`plugin_dir` is required when plugins are listed, each plugin must be a file
directly in it, and `apply:`, `wb.routes` and `wb.regen` are ignored (each
with a warning in `wb.warnings`). Plugins may import only the standard
library, `yaml`, `ruamel.yaml` and `curation_workbench`; the runner, not this
code, is the guard.
"""
import importlib
import sys
from pathlib import Path

from .config import Config, ConfigError


class PluginError(Exception):
    """A plugin that cannot be loaded (code `plugin_import_failed`)."""

    code = "plugin_import_failed"


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
        self.routes = []           # (method, prefix, fn(api, tail)) extra daemon routes
        self.suggestions = []      # review UI hooks (snapshot time only; see snapshot.py)
        self.browse_url = None
        self.subject_panels = []
        self.review_prompts = []
        self.snapshot_view = None  # set by snapshot.build while the review UI hooks run
        self.plugins = []
        self.hosted = False
        self.warnings = []         # hosted mode: what was ignored (carried in the snapshot bundle)
        from .store import YamlStore
        self.store = YamlStore(cfg.decisions)   # decision store; the hosted app passes a PgStore

    @classmethod
    def open(cls, config_path, out=None, root=None, plugins=True, store=None, hosted=False):
        """Load a project. `root` overrides the config's project tree (paths are
        re-resolved against it); `out` overrides where generated files go;
        `store` replaces the default decision store (the project's decisions.yaml);
        `hosted` applies the hosted-mode rules (module docstring) with `root`
        as the checkout."""
        cfg = Config.load(config_path, root=root)
        if out is not None:
            cfg.out = Path(out).resolve()
        if hosted:
            if root is None:
                raise ConfigError("config_invalid", "hosted mode needs root (the checkout)")
            cfg.check_contained(root)
            if plugins and cfg.plugins and cfg.plugin_dir is None:
                raise ConfigError("config_invalid",
                                  "plugin_dir is required in hosted mode when plugins are listed")
        wb = cls(cfg)
        wb.hosted = hosted
        if store is not None:
            wb.store = store
        from . import builtin
        builtin.register(wb)
        if plugins:
            wb.load_plugins()
        if hosted:
            wb._apply_hosted_rules()
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
        if self.hosted:
            for name in self.cfg.plugins:
                if not isinstance(name, str) or not name.isidentifier() \
                        or not (pdir / f"{name}.py").is_file():
                    raise PluginError(f"plugin {name!r} is not a module file in {self.cfg.rel(pdir)}")
        sys.path.insert(0, str(pdir))
        try:
            for name in self.cfg.plugins:
                if self.hosted:
                    sys.modules.pop(name, None)
                try:
                    mod = importlib.import_module(name)
                    if self.hosted and Path(mod.__file__).resolve().parent != pdir:
                        raise PluginError(f"plugin {name} did not come from {self.cfg.rel(pdir)}")
                    mod.register(self)
                except PluginError:
                    raise
                except Exception as e:
                    if not self.hosted:
                        raise
                    raise PluginError(f"plugin {name} failed to load: {type(e).__name__}: {e}") from e
                self.plugins.append(mod)
        finally:
            sys.path.remove(str(pdir))

    def _apply_hosted_rules(self):
        if self.cfg.data.get("apply"):
            self.warnings.append("the apply: section is ignored in hosted mode")
        if self.routes:
            self.warnings.append(f"{len(self.routes)} plugin daemon route(s) ignored in hosted mode")
            self.routes = []
        if self.regen:
            self.warnings.append(f"{len(self.regen)} plugin regen hook(s) ignored in hosted mode")
            self.regen = []

    def handler(self, obj):
        """Register an apply handler instance (keyed by its `kind` and `verdict`);
        a later registration for the same pair replaces the earlier one."""
        self.handlers[(obj.kind, obj.verdict)] = obj
        return obj

    def handler_check(self, kind, verdict, fn):
        self.handler_checks.setdefault((kind, verdict), []).append(fn)

    def option(self, key, default=None):
        return self.cfg.options.get(key, default)
