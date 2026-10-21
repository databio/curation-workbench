"""The repo's decision record: `<curation>/decisions.yaml`.

Decisions are made on the hosted site; this file is where they land
(`store.landing.append_hosted`, via `cwb pull`) and where the apply engine
stamps them applied (`mark_applied`). Nothing here creates, edits or
withdraws a decision. Every read and write goes through `yamlio`, so the
documented header and the file's style survive.
"""
import datetime
from pathlib import Path

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from .. import yamlio
from .validate import FIELDS, StoreError, to_plain


def _to_yaml(obj):
    """Plain Python -> ruamel nodes, block style, record fields in order."""
    if isinstance(obj, dict):
        m = CommentedMap()
        keys = [k for k in FIELDS if k in obj] + [k for k in obj if k not in FIELDS]
        for k in keys:
            m[k] = _to_yaml(obj[k])
        return m
    if isinstance(obj, (list, tuple)):
        s = CommentedSeq(_to_yaml(v) for v in obj)
        if not s or all(not isinstance(v, (dict, list)) for v in s):
            s.fa.set_flow_style()
        return s
    return obj


def _decisions_seq(doc):
    seq = doc.data["decisions"]
    seq.fa.set_block_style()
    return seq


def _as_date(value):
    """A plain `datetime.date` from an ISO string or a date (or None)."""
    if value is None:
        return None
    if isinstance(value, str):
        return datetime.date.fromisoformat(value)
    return datetime.date.fromordinal(value.toordinal())


class YamlStore:
    kind = "yaml"

    def __init__(self, path):
        self.path = Path(path)

    # ------------------------------------------------------------ reads

    def load(self):
        """The store as a yamlio.Doc (`.data` has version, next_id, decisions).
        The store always spells nulls out (`supersedes: null`)."""
        doc = yamlio.load(self.path)
        doc.nulls = True
        return doc

    def mtime_ns(self):
        return self.path.stat().st_mtime_ns

    def token(self):
        return str(self.mtime_ns())

    def records(self):
        """The records as plain JSON-safe dicts (dates as YYYY-MM-DD strings).

        Read the store through here, not PyYAML: YAML 1.1 loaders turn the
        `applied.on` key into the boolean True."""
        if not self.path.exists():
            return []
        return [to_plain(r) for r in self.load().data["decisions"]]

    @staticmethod
    def find(doc, dec_id):
        for i, rec in enumerate(doc.data["decisions"]):
            if rec["id"] == dec_id:
                return i, rec
        raise StoreError(404, [f"no decision {dec_id}"])

    def get(self, dec_id):
        _, rec = self.find(self.load(), dec_id)
        return to_plain(rec)

    def snapshot(self):
        """The whole store plus its `mtime_ns`."""
        doc = self.load()
        return {**to_plain(dict(doc.data)), "mtime_ns": self.mtime_ns()}

    # ------------------------------------------------------------ writes

    def mark_applied(self, stamps):
        """Set `status: applied` and the `applied` block of each id; one file write."""
        doc = self.load()
        by_id = {r["id"]: r for r in doc.data["decisions"]}
        missing = [d for d in stamps if d not in by_id]
        if missing:
            raise StoreError(404, [f"no decision {d}" for d in missing])
        for dec_id, block in stamps.items():
            rec = by_id[dec_id]
            rec["status"] = "applied"
            rec["applied"] = _to_yaml({**block, "on": _as_date(block.get("on"))})
        yamlio.dump(doc, self.path)
