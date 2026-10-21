"""Opaque, permanent term ids on permissible values.

Every permissible value carries its id as `meaning: <prefix>:<local>NNNNNNN`
(e.g. `ms:MS_0000123`). The label (the permissible-value key) may change;
the id never does, and an id is never reused.

The ledger (`terms.ledger`, a TSV) holds one row per id ever minted. It is the
allocation authority (next id = max + 1) and the only home of retired ids and
former labels. Live rows leave enum and label blank: the schema holds them.
The ledger's leading `#` comment block is kept verbatim on every write.
"""
from dataclasses import dataclass

import yaml

from .tsv import Table

LEDGER_COLUMNS = ["id", "status", "enum", "label", "replaced_by", "former_labels", "decision"]
STATUSES = ("live", "merged", "deleted", "faceted")
DEFAULT_HEADER = """\
# Term-id ledger. One row per id ever minted; ids are never reused.
#   status         live | merged | deleted | faceted
#   enum, label    blank for live rows (the schema holds them)
#   replaced_by    for merged / faceted rows, the id(s) that absorbed it (|-joined)
#   former_labels  labels this id carried before a rename (|-joined)
#   decision       the decision id that set the status
"""


@dataclass(frozen=True)
class Term:
    id: str | None     # local id, or None if the value has no valid meaning
    module: str
    enum: str
    label: str


class TermIds:
    def __init__(self, cfg):
        self.cfg = cfg
        self.id_re = cfg.term_id_re
        self.meaning_re = cfg.meaning_re

    # ---- id forms
    def meaning(self, tid):
        return f"{self.cfg.term_prefix}:{tid}"

    def from_meaning(self, value):
        """Local id from a `meaning` value, or None if it is not a term id."""
        value = str(value or "")
        return value.split(":", 1)[1] if self.meaning_re.match(value) else None

    def iri(self, tid):
        return f"{self.cfg.term_base}{tid}"

    # ---- schema
    def enum_modules(self):
        """Module names in the root schema's import order (the mint order)."""
        root = yaml.safe_load(open(self.cfg.schema_root))
        return [m for m in root.get("imports", []) if ":" not in m]

    def schema_terms(self):
        """Every permissible value in the schema, in import / enum / value order."""
        out = []
        for mod in self.enum_modules():
            d = yaml.safe_load(open(self.cfg.schema_dir / f"{mod}.yaml")) or {}
            for enum_name, enum_def in (d.get("enums") or {}).items():
                for label, pv in ((enum_def or {}).get("permissible_values") or {}).items():
                    out.append(Term(self.from_meaning((pv or {}).get("meaning")), mod, enum_name, label))
        return out

    # ---- ledger
    def read_table(self):
        if not self.cfg.ledger.exists():
            return Table([DEFAULT_HEADER], list(LEDGER_COLUMNS), [])
        return Table.parse(self.cfg.ledger.read_text())

    def read_ledger(self):
        return self.read_table().rows

    def write_ledger(self, rows, table=None):
        table = table or self.read_table()
        table.rows = sorted(rows, key=lambda r: r["id"])
        self.cfg.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.ledger.write_text(table.dumps())

    def live_ids(self):
        return {r["id"] for r in self.read_ledger() if r["status"] == "live"}

    def allocate(self, count=1):
        """Mint `count` new ids (max + 1 onward), record them as live, return them."""
        table = self.read_table()
        local, n = self.cfg.term_local, self.cfg.term_digits
        top = max((int(r["id"][len(local):]) for r in table.rows), default=0)
        new = [f"{local}{top + i:0{n}d}" for i in range(1, count + 1)]
        self.write_ledger(table.rows + [{"id": i, "status": "live"} for i in new], table)
        return new

    def resolve_label(self, label, enum=None):
        """Id for a live label (or a former / retired label); None if unknown.
        Raises ValueError if ambiguous across enums and `enum` is not given."""
        hits = {t.id for t in self.schema_terms()
                if t.label == label and (enum is None or t.enum == enum)}
        if not hits:
            hits = {r["id"] for r in self.read_ledger()
                    if label in (r.get("former_labels") or "").split("|")
                    or (r["status"] != "live" and r.get("label") == label
                        and (enum is None or r.get("enum") == enum))}
        hits.discard(None)
        if len(hits) > 1:
            raise ValueError(f"label {label!r} is ambiguous: {sorted(hits)}")
        return next(iter(hits), None)
