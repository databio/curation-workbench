"""Project configuration: `workbench.yaml`.

A project is a directory holding a LinkML schema (one or more module files),
optional SSSOM mapping files and a curation folder. `workbench.yaml` says where
each of those lives and which plugins to load. Paths in the file are relative
to `root`, and `root` is relative to the config file itself.

    project: myschema
    root: .                      # the project tree
    schema:
      dir: src                   # module files: <dir>/*.yaml
      root: myschema.yaml        # its `imports:` order is the term mint order
      exclude: [linkml_lint_config.yaml]
      layer_annotation: null     # optional module annotation holding an int layer
    terms:                       # opaque permanent term ids on permissible values
      prefix: ms                 # `meaning: ms:MS_0000001`
      local: MS_
      digits: 7
      base: https://example.org/ms/
      ledger: curation/term_ids.tsv
    mappings:
      sssom: "mappings/*.sssom.tsv"
      columns:                   # subject payload key -> SSSOM column
        predicate: predicate_id
        object: object_id
        modifier: predicate_modifier
        justification: mapping_justification
        comment: comment
    curation:
      dir: curation              # decisions, verdicts, policy, aliases, findings
    out: curation                # where generated files go (cwb --out overrides)
    review_types: null           # review tracks/types YAML, relative to this config
                                 # file (default: <curation>/review_types.yaml)
    plugins: []                  # module names, importable from the config's folder
    plugin_dir: null             # folder holding the plugin modules (and their helper
                                 # modules), relative to this config file. When set,
                                 # plugins import from there only
    snapshot: null               # where `cwb regen` / `cwb snapshot` write the snapshot
                                 # bundle, relative to this config file. The repo commits
                                 # it; the website reads it (curation/snapshot.json)
    options: {}                  # free-form settings read by generic signals/plugins

Everything optional has a default; see DEFAULTS.
"""
import copy
import re
from pathlib import Path

import yaml

DEFAULTS = {
    "project": None,
    "root": ".",
    "schema": {"dir": "src", "root": None, "exclude": [], "layer_annotation": None},
    "terms": {"prefix": None, "local": None, "digits": 7, "base": None,
              "ledger": "curation/term_ids.tsv"},
    "mappings": {"sssom": "mappings/*.sssom.tsv",
                 "columns": {"predicate": "predicate_id", "object": "object_id",
                             "modifier": "predicate_modifier",
                             "justification": "mapping_justification",
                             "comment": "comment"}},
    "curation": {"dir": "curation"},
    "out": None,
    "review_types": None,
    "plugins": [],
    "plugin_dir": None,
    "snapshot": None,
    "options": {},
}


class ConfigError(Exception):
    """A workbench.yaml that cannot be used (`code` is `config_invalid`)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "columns":
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    """A loaded workbench.yaml with every path resolved."""

    def __init__(self, data, path, root=None):
        self.path = Path(path).resolve()
        self.data = _merge(DEFAULTS, data)
        d = self.data
        self.name = d["project"] or self.path.parent.name
        self.root = Path(root).resolve() if root else (self.path.parent / d["root"]).resolve()
        s = d["schema"]
        self.schema_dir = self.root / s["dir"]
        self.schema_root = self.schema_dir / s["root"] if s["root"] else None
        self.schema_exclude = set(s["exclude"] or [])
        self.layer_annotation = s["layer_annotation"]
        t = d["terms"]
        self.term_prefix = t["prefix"]
        self.term_local = t["local"]
        self.term_digits = int(t["digits"])
        self.term_base = t["base"]
        self.ledger = self.root / t["ledger"]
        self.sssom_glob = d["mappings"]["sssom"]
        self.sssom_columns = dict(d["mappings"]["columns"])
        self.curation = self.root / d["curation"]["dir"]
        self.out = self.root / d["out"] if d["out"] else self.curation
        self.plugins = list(d["plugins"] or [])
        self.plugin_dir = (self.path.parent / d["plugin_dir"]).resolve() if d["plugin_dir"] else None
        self.snapshot = (self.path.parent / d["snapshot"]).resolve() if d["snapshot"] else None
        self.options = d["options"] or {}

    @classmethod
    def load(cls, path, root=None):
        try:
            data = yaml.safe_load(Path(path).read_text()) or {}
        except (OSError, yaml.YAMLError) as e:
            raise ConfigError("config_invalid", f"cannot read {path}: {e}") from None
        if not isinstance(data, dict):
            raise ConfigError("config_invalid", f"{path} is not a YAML mapping")
        try:
            return cls(data, path, root=root)
        except (TypeError, ValueError, KeyError) as e:
            raise ConfigError("config_invalid", f"{path}: {type(e).__name__}: {e}") from None

    # ---- term ids
    @property
    def has_term_ids(self):
        return bool(self.term_prefix and self.term_local)

    @property
    def term_id_re(self):
        return re.compile(rf"^{re.escape(self.term_local)}\d{{{self.term_digits}}}$")

    @property
    def meaning_re(self):
        return re.compile(rf"^{re.escape(self.term_prefix)}:{re.escape(self.term_local)}\d{{{self.term_digits}}}$")

    # ---- curation files
    def cur(self, name):
        return self.curation / name

    @property
    def decisions(self):
        return self.cur("decisions.yaml")

    @property
    def verdicts(self):
        return self.cur("verdicts.yaml")

    @property
    def policy(self):
        return self.cur("policy.yaml")

    @property
    def review_types(self):
        rt = self.data.get("review_types")
        return (self.path.parent / rt).resolve() if rt else self.cur("review_types.yaml")

    @property
    def findings_dir(self):
        """Generated findings files (one JSON per source), under the output dir."""
        return self.out / "findings"

    def output(self, name):
        """Where a generated file is written (and read back by later stages)."""
        return self.out / name

    def sssom_files(self):
        return sorted(self.root.glob(self.sssom_glob))

    def rel(self, p):
        try:
            return str(Path(p).relative_to(self.root))
        except ValueError:
            return str(p)
