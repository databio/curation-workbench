"""Reading the project: LinkML module files and SSSOM mapping rows.

Read-only helpers (PyYAML / csv). Writers go through yamlio / tsv.
"""
import csv

import yaml


def load_modules(cfg):
    """{module name: parsed YAML} for every <schema dir>/*.yaml not excluded."""
    out = {}
    for path in sorted(cfg.schema_dir.glob("*.yaml")):
        if path.name in cfg.schema_exclude:
            continue
        out[path.stem] = yaml.safe_load(open(path)) or {}
    return out


def module_path(cfg, mod):
    return cfg.schema_dir / f"{mod}.yaml"


def module_rel(cfg, mod):
    return cfg.rel(module_path(cfg, mod))


def slot_ranges(body):
    """Every range named by a slot body (range, any_of / exactly_one_of / ... ranges)."""
    out = [body["range"]] if body.get("range") else []
    for key in ("any_of", "exactly_one_of", "all_of", "none_of"):
        for branch in body.get(key) or []:
            if (branch or {}).get("range"):
                out.append(branch["range"])
    return out


def read_sssom(path):
    """Data rows of one SSSOM TSV as dicts keyed by header name (comments skipped)."""
    with open(path) as fh:
        return list(csv.DictReader(
            (line for line in fh if not line.startswith("#")), delimiter="\t"))


def all_sssom_rows(cfg):
    """[(file name, row)] over every SSSOM file, in file then row order."""
    return [(p.name, r) for p in cfg.sssom_files() for r in read_sssom(p)]


def is_negated(row):
    return (row.get("predicate_modifier") or "").strip() == "Not"
