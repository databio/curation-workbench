"""Decision and vote validation, shared by every store backend.

Nothing here touches a file or a database. `validate` is the one gate every
decision write goes through (YAML or Postgres); `validate_vote` is the gate for
votes. Neither backend adds rules of its own.

The verdict vocabulary comes from `verdicts.yaml` and the subject registry from
`subjects.json`; nothing here hard-codes a verdict or a kind. Project rules
reach validation through the workbench hooks `operation_checks` and
`record_checks` (pass `wb=`).
"""
import copy
import datetime
import json
import re
from pathlib import Path

from .. import yamlio

STATUSES = ("pending", "applied", "withdrawn")
ORIGINS = ("local", "seed", "github_issue", "github_pr", "hosted")
EVIDENCE_KINDS = ("finding", "usage", "issue", "pr", "doc", "other")
# DEC-NNNN: minted by the YAML store (the repo's own sequence).
# H-NNNN: minted by the hosted (Postgres) store, per project.
ID_RE = re.compile(r"^(DEC|H)-\d{4,}$")
HOSTED_ID_RE = re.compile(r"^H-\d{4,}$")
STALE_REASONS = ("subject_changed", "renamed", "subject_removed")
ANSWER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
PROPOSAL_KEYS = ("verdict", "operation", "rationale", "also_affects", "evidence")
VOTE_KEYS = ("subject", "review_type", "answer", "proposal", "comment", "subject_hash",
             "schema_fingerprint")

# Field order of a record as written to the store.
FIELDS = (
    "id", "subject", "also_affects", "verdict", "operation", "rationale",
    "evidence", "decided_by", "decided_on", "subject_hashes",
    "schema_fingerprint", "status", "applied", "supersedes", "origin",
)


class StoreError(Exception):
    """A rejected write. `status` is the HTTP code a server returns:
    400 for a malformed record, 404 for an unknown id, 409 for a conflict
    (a subject changed since the client read it, or a stale version token).
    `current` carries the current record when a revision check failed."""

    def __init__(self, status, errors, current=None):
        self.status = status
        self.errors = list(errors)
        self.current = current
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


def to_plain(obj):
    """ruamel/date values -> JSON-safe plain Python."""
    if isinstance(obj, dict):
        return {str(k): to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_plain(v) for v in obj]
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    return obj


def normalize(rec):
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


# ---------------------------------------------------------------- state rules

def check_updatable(current):
    """Only pending records can change."""
    if current["status"] != "pending":
        raise StoreError(400, [f"{current['id']} is {current['status']}; only pending records can change"])


def check_withdrawable(current):
    """Applied records are never withdrawn (supersede them instead)."""
    if current["status"] == "applied":
        raise StoreError(400, [f"{current['id']} is applied; record a superseding decision instead"])


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


def _check_hosted_origin(dec_id, origin, errors):
    """A hosted record carries its own H- id in origin.ref; once exported into a
    repo file it has a DEC- id and origin.ref keeps the H- id it came from."""
    ref = origin.get("ref")
    if not isinstance(dec_id, str):
        return
    if dec_id.startswith("H-"):
        if ref != dec_id:
            errors.append(f"origin.ref of hosted decision {dec_id} must be {dec_id}")
    elif not (isinstance(ref, str) and HOSTED_ID_RE.match(ref)):
        errors.append("origin.ref of an exported hosted decision must be its H- id")


def validate(rec, subjects, verdicts, known_ids=None, check_hashes=True, wb=None):
    """Raise StoreError unless `rec` may be written.

    400 for any structural problem; 409 when the record is otherwise fine but a
    `subject_hashes` entry no longer matches the registry (the subject changed
    since the client read it). `known_ids` is the set of decision ids that
    `supersedes` may name. `check_hashes=False` is for the apply engine, which
    re-stamps hashes itself.
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
    elif origin["kind"] == "hosted":
        _check_hosted_origin(dec_id, origin, errors)
    elif isinstance(dec_id, str) and dec_id.startswith("H-"):
        errors.append(f"{dec_id} is a hosted id; origin.kind must be hosted")

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
        if sup not in (known_ids or set()) or sup == dec_id:
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


# ---------------------------------------------------------------- votes

def _review_type_kinds(review_types, name):
    """The subject kinds review type `track/type` covers, or None if it does not exist."""
    track_id, _, type_id = name.partition("/")
    for track in (review_types or {}).get("tracks") or []:
        if track.get("id") != track_id:
            continue
        if any(t.get("id") == type_id for t in track.get("types") or []):
            return list(track.get("kinds") or [])
    return None


def validate_vote(vote, ctx):
    """Raise StoreError unless `vote` may be recorded; return it normalized.

    `vote` is {subject, review_type, answer, proposal?, comment?, subject_hash}
    (plus an optional schema_fingerprint the caller sets). 400 for a malformed
    vote; 409 when `subject_hash` is not the subject's current hash, so a vote is
    never recorded against a version the voter did not see.
    """
    if not isinstance(vote, dict):
        raise StoreError(400, ["a vote must be a mapping"])
    errors = [f"{k} is not a field of a vote" for k in vote if k not in VOTE_KEYS]
    subjects, verdicts = ctx.subjects, ctx.verdicts

    sid = vote.get("subject")
    subj = subjects.get(sid) if isinstance(sid, str) else None
    if subj is None:
        raise StoreError(400, errors + [f"unknown subject {sid!r}"])
    if subj.get("retired"):
        errors.append(f"{sid} is retired")
    kind = subj["kind"]

    rt = vote.get("review_type")
    if rt != "subject":
        kinds = _review_type_kinds(ctx.review_types, rt) if isinstance(rt, str) else None
        if kinds is None:
            errors.append(f"unknown review type {rt!r}")
        elif kind not in kinds:
            errors.append(f"{sid} is a {kind}; review type {rt} covers {kinds}")

    answer = vote.get("answer")
    if not isinstance(answer, str) or not ANSWER_RE.match(answer):
        errors.append(f"answer must match {ANSWER_RE.pattern}, got {answer!r}")

    proposal = vote.get("proposal")
    if proposal is not None:
        if not isinstance(proposal, dict):
            errors.append("proposal must be a mapping")
            proposal = None
        else:
            proposal = to_plain(proposal)
            errors += [f"proposal.{k} is not a field of a proposal"
                       for k in proposal if k not in PROPOSAL_KEYS]
            pv = proposal.get("verdict")
            entry = verdicts.get(kind, {}).get(pv)
            if entry is None:
                errors.append(f"proposal.verdict {pv!r} is not allowed for a {kind}; "
                              f"allowed: {sorted(verdicts.get(kind, {}))}")
            else:
                op_errors, _ = check_operation(pv, entry, proposal.get("operation"), subjects, wb=ctx.wb)
                errors += [f"proposal: {e}" for e in op_errors]
            if "rationale" in proposal and not isinstance(proposal["rationale"], str):
                errors.append("proposal.rationale must be a string")
            also = proposal.get("also_affects", [])
            if not isinstance(also, list):
                errors.append("proposal.also_affects must be a list of subject ids")
            else:
                for other in also:
                    if subjects.get(other) is None:
                        errors.append(f"proposal.also_affects: unknown subject {other!r}")
                    elif other == sid:
                        errors.append("proposal.also_affects must not repeat the subject")
            ev = proposal.get("evidence", [])
            if not isinstance(ev, list) or not all(
                    isinstance(e, dict) and e.get("kind") in EVIDENCE_KINDS for e in ev):
                errors.append(f"proposal.evidence must be a list of {{kind, id?, detail?}}, "
                              f"kind in {list(EVIDENCE_KINDS)}")
    if rt == "subject" and (proposal is None or proposal.get("verdict") != answer):
        errors.append("a vote on the subject itself needs a proposal whose verdict is the answer")

    comment = vote.get("comment", "")
    if comment is None:
        comment = ""
    if not isinstance(comment, str):
        errors.append("comment must be a string")

    h = vote.get("subject_hash")
    if not _is_str(h):
        errors.append("subject_hash is required")
    if errors:
        raise StoreError(400, errors)
    if h != subj["hash"]:
        raise StoreError(409, [f"{sid} changed since it was read (recorded {h}, current {subj['hash']})"])

    return {"subject": sid, "review_type": rt, "answer": answer, "proposal": proposal,
            "comment": comment, "subject_hash": h,
            "schema_fingerprint": vote.get("schema_fingerprint") or subjects.fingerprint}
