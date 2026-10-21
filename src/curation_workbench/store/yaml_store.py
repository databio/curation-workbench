"""The local decision store: `<curation>/decisions.yaml`.

Every read and write goes through `yamlio`, so the documented header and the
file's style survive. Writes take one module-level lock and do a full
read-modify-write. Optimistic concurrency is whole-file: the token is the
file's `mtime_ns`, compared inside the lock before the read.
"""
import datetime
import threading
from pathlib import Path

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from .. import yamlio
from .validate import (FIELDS, StoreError, check_updatable, check_withdrawable,
                       normalize, to_plain, validate)

# The daemon also holds this for whole requests (it is reentrant).
LOCK = threading.RLock()


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
        """The whole store plus its `mtime_ns` (the daemon's GET /api/decisions body)."""
        doc = self.load()
        return {**to_plain(dict(doc.data)), "mtime_ns": self.mtime_ns()}

    # ------------------------------------------------------------ writes

    def _check_token(self, expect_token):
        if expect_token is not None and str(expect_token) != self.token():
            raise StoreError(409, ["decisions.yaml changed since it was read; reload"])

    @staticmethod
    def _known_ids(doc, ctx):
        return {r["id"] for r in doc.data["decisions"]} | set(ctx.extra_known_ids)

    def create(self, rec, ctx, *, expect_token=None, vote_ids=()):
        """Validate and append `rec`; mint its id from `next_id`. Returns the record."""
        if vote_ids:
            raise StoreError(400, ["votes need the hosted store"])
        with LOCK:
            self._check_token(expect_token)
            doc = self.load()
            rec = normalize(rec)
            rec["id"] = f"DEC-{int(doc.data['next_id']):04d}"
            rec.setdefault("decided_on", datetime.date.today())
            rec.setdefault("schema_fingerprint", ctx.subjects.fingerprint)
            validate(rec, ctx.subjects, ctx.verdicts, known_ids=self._known_ids(doc, ctx), wb=ctx.wb)
            _decisions_seq(doc).append(_to_yaml(rec))
            doc.data["next_id"] = int(doc.data["next_id"]) + 1
            yamlio.dump(doc, self.path)
            return to_plain(rec)

    def update(self, dec_id, rec, ctx, *, expect_token=None, expect_rev=None, check_hashes=True):
        """Validate and replace pending record `dec_id` with `rec` (whose id must match)."""
        if expect_rev is not None:
            raise StoreError(400, ["record revisions need the hosted store"])
        with LOCK:
            self._check_token(expect_token)
            doc = self.load()
            i, cur = self.find(doc, dec_id)
            check_updatable(cur)
            rec = normalize(rec)
            if rec.setdefault("id", dec_id) != dec_id:
                raise StoreError(400, [f"record id {rec['id']} does not match {dec_id}"])
            validate(rec, ctx.subjects, ctx.verdicts, known_ids=self._known_ids(doc, ctx),
                     check_hashes=check_hashes, wb=ctx.wb)
            _decisions_seq(doc)[i] = _to_yaml(rec)
            yamlio.dump(doc, self.path)
            return to_plain(rec)

    def withdraw(self, dec_id, *, expect_token=None, expect_rev=None):
        """Set `status: withdrawn`. Records are never removed."""
        if expect_rev is not None:
            raise StoreError(400, ["record revisions need the hosted store"])
        with LOCK:
            self._check_token(expect_token)
            doc = self.load()
            _, rec = self.find(doc, dec_id)
            check_withdrawable(rec)
            rec["status"] = "withdrawn"
            yamlio.dump(doc, self.path)
            return to_plain(rec)

    def mark_applied(self, stamps):
        """Set `status: applied` and the `applied` block of each id; one file write."""
        with LOCK:
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
