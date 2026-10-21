"""The decision store: `<curation>/decisions.yaml`.

Every read and write goes through `yamlio`, so the documented header and the
file's style survive. `create` / `update` / `withdraw` take one module-level
lock and do a full read-modify-write; the daemon adds optimistic concurrency on
top by comparing the file's `mtime_ns` (see `mtime_ns()`).

Validation (`validate`) is the one gate every writer uses. It reads the verdict
vocabulary from `verdicts.yaml` and the subject registry from `subjects.json`;
nothing here hard-codes a verdict or a kind. Project rules reach it through the
workbench hooks `operation_checks` and `record_checks` (pass `wb=`).
"""
import copy
import datetime
import json
import re
import threading
from pathlib import Path

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from . import yamlio

STATUSES = ("pending", "applied", "withdrawn")
ORIGINS = ("local", "seed", "github_issue", "github_pr")
EVIDENCE_KINDS = ("finding", "usage", "issue", "pr", "doc", "other")
ID_RE = re.compile(r"^DEC-\d{4,}$")

# Field order of a record as written to the store.
FIELDS = (
    "id", "subject", "also_affects", "verdict", "operation", "rationale",
    "evidence", "decided_by", "decided_on", "subject_hashes",
    "schema_fingerprint", "status", "applied", "supersedes", "origin",
)

LOCK = threading.RLock()


class StoreError(Exception):
    """A rejected write. `status` is the HTTP code the daemon returns:
    400 for a malformed record, 404 for an unknown id, 409 for a conflict
    (a subject changed since the client read it)."""

    def __init__(self, status, errors):
        self.status = status
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


# ---------------------------------------------------------------- inputs

def load_verdicts(path):
    """{kind: {verdict: entry}} from verdicts.yaml."""
    data = yamlio.load(path).data
    return {k: {v: dict(e) for v, e in vs.items()} for k, vs in data["verdicts"].items()}


class Subjects:
    """Index over subjects.json: `get(sid)`, `fingerprint`."""

    def __init__(self, data):
        self.fingerprint = data["schema_fingerprint"]
        self.by_sid = data["subjects"]

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text()))

    def get(self, sid):
        return self.by_sid.get(sid)


# ---------------------------------------------------------------- store io

def load(path):
    """The store as a yamlio.Doc (`.data` has version, next_id, decisions).
    The store always spells nulls out (`supersedes: null`)."""
    doc = yamlio.load(path)
    doc.nulls = True
    return doc


def mtime_ns(path):
    return Path(path).stat().st_mtime_ns


def records(path):
    return list(load(path).data["decisions"])


def plain_records(path):
    """The records as plain JSON-safe dicts (dates as YYYY-MM-DD strings).

    Read the store through here, not PyYAML: YAML 1.1 loaders turn the
    `applied.on` key into the boolean True."""
    if not Path(path).exists():
        return []
    return [to_plain(r) for r in records(path)]


def find(doc, dec_id):
    for i, rec in enumerate(doc.data["decisions"]):
        if rec["id"] == dec_id:
            return i, rec
    raise StoreError(404, [f"no decision {dec_id}"])


def to_plain(obj):
    """ruamel/date values -> JSON-safe plain Python."""
    if isinstance(obj, dict):
        return {str(k): to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_plain(v) for v in obj]
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    return obj


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


def _normalize(rec):
    """Fill defaults for the optional parts of a record (plain dict in, out)."""
    rec = copy.deepcopy(to_plain(rec))
    rec.setdefault("also_affects", [])
    rec.setdefault("operation", None)
    rec.setdefault("rationale", "")
    rec.setdefault("evidence", [])
    rec.setdefault("status", "pending")
    rec.setdefault("applied", {"on": None, "subject_hashes_after": None, "created": [], "retired": []})
    rec.setdefault("supersedes", None)
    rec.setdefault("origin", {"kind": "local", "ref": None})
    if isinstance(rec.get("decided_on"), str):
        try:
            rec["decided_on"] = datetime.date.fromisoformat(rec["decided_on"])
        except ValueError:
            pass
    return rec


def create(rec, subjects, verdicts, path, wb=None):
    """Validate and append `rec`; mint its id from `next_id`. Returns the record."""
    with LOCK:
        doc = load(path)
        rec = _normalize(rec)
        rec["id"] = f"DEC-{int(doc.data['next_id']):04d}"
        rec.setdefault("decided_on", datetime.date.today())
        rec.setdefault("schema_fingerprint", subjects.fingerprint)
        validate(rec, subjects, verdicts, doc=doc, wb=wb)
        _decisions_seq(doc).append(_to_yaml(rec))
        doc.data["next_id"] = int(doc.data["next_id"]) + 1
        yamlio.dump(doc, path)
        return to_plain(rec)


def update(dec_id, rec, subjects, verdicts, path, check_hashes=True, wb=None):
    """Validate and replace record `dec_id` with `rec` (whose id must match)."""
    with LOCK:
        doc = load(path)
        i, _ = find(doc, dec_id)
        rec = _normalize(rec)
        if rec.setdefault("id", dec_id) != dec_id:
            raise StoreError(400, [f"record id {rec['id']} does not match {dec_id}"])
        validate(rec, subjects, verdicts, doc=doc, check_hashes=check_hashes, wb=wb)
        _decisions_seq(doc)[i] = _to_yaml(rec)
        yamlio.dump(doc, path)
        return to_plain(rec)


def withdraw(dec_id, path):
    """Set `status: withdrawn`. Records are never removed."""
    with LOCK:
        doc = load(path)
        _, rec = find(doc, dec_id)
        if rec["status"] == "applied":
            raise StoreError(400, [f"{dec_id} is applied; record a superseding decision instead"])
        rec["status"] = "withdrawn"
        yamlio.dump(doc, path)
        return to_plain(rec)


# ---------------------------------------------------------------- validation

def _is_str(v):
    return isinstance(v, str) and v.strip() != ""


def _check_type(field, value, spec, subjects, errors, sids_out):
    nullable = spec.endswith("?")
    spec = spec.rstrip("?")
    if value is None:
        if not nullable:
            errors.append(f"operation.{field} must not be null")
        return

    def sid_ok(sid, kind):
        if not isinstance(sid, str):
            errors.append(f"operation.{field}: {sid!r} is not a subject id")
            return
        subj = subjects.get(sid)
        if subj is None:
            errors.append(f"operation.{field}: unknown subject {sid}")
        elif kind and subj["kind"] != kind:
            errors.append(f"operation.{field}: {sid} is a {subj['kind']}, expected {kind}")
        elif subj.get("retired"):
            errors.append(f"operation.{field}: {sid} is retired")
        else:
            sids_out.append(sid)

    base, _, arg = spec.partition(":")
    if base == "str":
        if not _is_str(value):
            errors.append(f"operation.{field} must be a non-empty string")
    elif base == "bool":
        if not isinstance(value, bool):
            errors.append(f"operation.{field} must be true or false")
    elif base == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"operation.{field} must be an integer")
    elif base == "map":
        if not isinstance(value, dict):
            errors.append(f"operation.{field} must be a mapping")
    elif base == "list":
        if not isinstance(value, list):
            errors.append(f"operation.{field} must be a list")
    elif base == "str_list":
        if not isinstance(value, list) or not all(_is_str(v) for v in value):
            errors.append(f"operation.{field} must be a list of non-empty strings")
    elif base == "choice":
        if value not in arg.split("|"):
            errors.append(f"operation.{field} must be one of {arg.split('|')}")
    elif base == "sid":
        sid_ok(value, arg or None)
    elif base == "sids":
        if not isinstance(value, list):
            errors.append(f"operation.{field} must be a list of subject ids")
        else:
            for sid in value:
                sid_ok(sid, arg or None)
    else:
        raise ValueError(f"verdicts.yaml: unknown field type {spec!r} for {field}")


def check_operation(verdict, entry, op, subjects, wb=None):
    """Errors in `op` against the verdict's operation schema, plus the SIDs it
    references (all of which exist)."""
    errors, sids = [], []
    schema = entry.get("operation") or {}
    required = schema.get("required") or {}
    optional = schema.get("optional") or {}
    one_of = schema.get("one_of") or []
    if op is None:
        if required or one_of:
            errors.append(f"verdict {verdict} needs an operation")
        return errors, sids
    if not isinstance(op, dict):
        return ["operation must be a mapping"], sids
    if op.get("op") != verdict:
        errors.append(f"operation.op must be {verdict!r}")
    for field in op:
        if field != "op" and field not in required and field not in optional:
            errors.append(f"operation.{field} is not a field of {verdict}")
    for field in required:
        if field not in op:
            errors.append(f"operation.{field} is required for {verdict}")
    for field, spec in {**required, **optional}.items():
        if field in op:
            _check_type(field, op[field], spec, subjects, errors, sids)
    for check in (wb.operation_checks if wb else []):
        errors += check(verdict, op)
    if one_of:
        present = [g for g in one_of if any(f in op for f in g)]
        if len(present) != 1:
            names = " | ".join("/".join(g) for g in one_of)
            errors.append(f"operation for {verdict} needs exactly one of: {names}")
    return errors, sids


def validate(rec, subjects, verdicts, doc=None, check_hashes=True, wb=None):
    """Raise StoreError unless `rec` may be written.

    400 for any structural problem; 409 when the record is otherwise fine but a
    `subject_hashes` entry no longer matches the registry (the subject changed
    since the client read it). `check_hashes=False` is for the apply engine,
    which re-stamps hashes itself.
    """
    errors, conflicts = [], []

    dec_id = rec.get("id")
    if not isinstance(dec_id, str) or not ID_RE.match(dec_id):
        errors.append(f"id must look like DEC-0001, got {dec_id!r}")

    sid = rec.get("subject")
    subj = subjects.get(sid) if isinstance(sid, str) else None
    if subj is None:
        errors.append(f"unknown subject {sid!r}")
        raise StoreError(400, errors)
    kind = subj["kind"]

    verdict = rec.get("verdict")
    entry = verdicts.get(kind, {}).get(verdict)
    if entry is None:
        errors.append(f"verdict {verdict!r} is not allowed for a {kind}; "
                      f"allowed: {sorted(verdicts.get(kind, {}))}")
        raise StoreError(400, errors)

    status = rec.get("status")
    if status not in STATUSES:
        errors.append(f"status must be one of {list(STATUSES)}")

    origin = rec.get("origin")
    if not isinstance(origin, dict) or origin.get("kind") not in ORIGINS:
        errors.append(f"origin.kind must be one of {list(ORIGINS)}")
    elif entry.get("seed_only") and origin["kind"] != "seed":
        errors.append(f"verdict {verdict} is historical and only the seed may record it")

    record_only = entry["writes"] == "none"
    if subj.get("retired") and not record_only:
        errors.append(f"{sid} is retired; only record-only verdicts are allowed")

    if entry["terminal"] and not _is_str(rec.get("rationale")):
        errors.append(f"a {verdict} decision needs a rationale")
    elif rec.get("rationale") is not None and not isinstance(rec.get("rationale"), str):
        errors.append("rationale must be a string")

    for check in (wb.record_checks if wb else []):
        errors += check(wb, rec)

    op_errors, _ = check_operation(verdict, entry, rec.get("operation"), subjects, wb=wb)
    errors += op_errors

    also = rec.get("also_affects")
    if not isinstance(also, list):
        errors.append("also_affects must be a list of subject ids")
        also = []
    for other in also:
        if subjects.get(other) is None:
            errors.append(f"also_affects: unknown subject {other!r}")
        elif other == sid:
            errors.append("also_affects must not repeat the subject")

    ev = rec.get("evidence")
    if not isinstance(ev, list) or not all(
            isinstance(e, dict) and e.get("kind") in EVIDENCE_KINDS for e in ev):
        errors.append(f"evidence must be a list of {{kind, id?, detail?}}, kind in {list(EVIDENCE_KINDS)}")

    if not _is_str(rec.get("decided_by")):
        errors.append("decided_by is required")
    if not isinstance(rec.get("decided_on"), datetime.date):
        errors.append("decided_on must be a YYYY-MM-DD date")
    if not _is_str(rec.get("schema_fingerprint")):
        errors.append("schema_fingerprint is required")

    sup = rec.get("supersedes")
    if sup is not None:
        known = {r["id"] for r in (doc.data["decisions"] if doc is not None else [])}
        if sup not in known or sup == dec_id:
            errors.append(f"supersedes: no earlier decision {sup!r}")

    hashes = rec.get("subject_hashes")
    if not isinstance(hashes, dict):
        errors.append("subject_hashes must map each affected subject to its hash")
    else:
        for need in [sid, *also]:
            if need not in hashes:
                errors.append(f"subject_hashes is missing {need}")
        for hsid, h in hashes.items():
            cur = subjects.get(hsid)
            if cur is None:
                errors.append(f"subject_hashes: unknown subject {hsid!r}")
            elif check_hashes and status == "pending" and h != cur["hash"]:
                conflicts.append(f"{hsid} changed since it was read "
                                 f"(recorded {h}, current {cur['hash']})")

    if errors:
        raise StoreError(400, errors)
    if conflicts:
        raise StoreError(409, conflicts)
