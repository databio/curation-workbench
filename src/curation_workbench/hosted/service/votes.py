"""Votes, questions (tallies), and a curator turning a question into a decision.

A **question** is one subject in one review type: `(sid, review_type)`, where
`review_type` is `"<track>/<type>"` or `"subject"` (a vote cast from a subject
page). Each team member has at most one vote per question. Content checks are
plan 1's `validate_vote` (inside `PgStore.cast_vote`); this module only adds
the rules that need the team or the question's state:

- a vote marked stale (the subject changed in a newer snapshot) is shown but not counted; voting
  again clears the mark;
- votes of former members (removed from the team) are shown with
  `former_member: true` and not counted;
- a resolved question refuses votes (409 `question_resolved`) until a curator
  reopens it;
- votes never become decisions on their own: a curator resolves the question
  (`resolve`), with a decision (from a vote's proposal or a full record) or
  with no action, in one transaction;
- every write runs the staleness pass (`sync.publish.refresh_stale`).
"""
from __future__ import annotations

from snapgate import Caller, Conflict
from sqlalchemy import select

from ...store import StoreError
from ...store.pg import PgStore
from ...store.pg.tables import decisions, projects
from ...store.validate import _review_type_kinds
from .. import project_data
from ..sync.publish import refresh_stale
from ..tables import question_resolutions
from . import roles
from .decisions import client_record, decided_by
from .errors import BadRequest, DecisionStillActive, QuestionResolved
from .projects import USERS

OUTCOMES = ("decision", "no_action")


# ------------------------------------------------------------------ shapes

def _names(conn, user_ids) -> dict[int, str]:
    ids = sorted({u for u in user_ids if u is not None})
    if not ids:
        return {}
    return dict(conn.execute(select(USERS.c.id, USERS.c.name).where(USERS.c.id.in_(ids))).all())


def vote_out(v: dict, names: dict, members: dict) -> dict:
    """A store vote as the API's Vote."""
    return {"id": v["id"], "sid": v["subject"], "review_type": v["review_type"],
            "answer": v["answer"], "proposal": v["proposal"], "comment": v["comment"],
            "user": {"id": v["user_id"], "name": names.get(v["user_id"], "")},
            "subject_hash": v["subject_hash"], "stale": v["stale"],
            "stale_reason": v["stale_reason"], "rev": v["rev"],
            "created_at": v["created_at"], "updated_at": v["updated_at"],
            "former_member": v["user_id"] not in members}


def _votes(conn, project_id, sid=None, review_type=None, user_id=None) -> list[dict]:
    store = PgStore(conn.engine, project_id)
    with store.using(conn):
        return store.votes(subject=sid, review_type=review_type, user_id=user_id)


def _resolutions(conn, project_id, sid=None, review_type=None) -> dict[tuple, dict]:
    """The newest resolution row per question, with the decision's H- id."""
    q = (select(question_resolutions, decisions.c.dec_id, decisions.c.status.label("decision_status"))
         .select_from(question_resolutions.outerjoin(
             decisions, decisions.c.id == question_resolutions.c.decision_id))
         .where(question_resolutions.c.project_id == project_id)
         .order_by(question_resolutions.c.id))
    if sid is not None:
        q = q.where(question_resolutions.c.subject == sid)
    if review_type is not None:
        q = q.where(question_resolutions.c.review_type == review_type)
    out = {}
    for r in conn.execute(q):
        out[(r.subject, r.review_type)] = dict(r._mapping)
    return out


def _state(res) -> dict:
    if res is None or res["state"] != "resolved":
        return {"state": "open", "outcome": None, "decision_id": None}
    return {"state": "resolved", "outcome": res["outcome"], "decision_id": res["dec_id"]}


def question_states(conn, project_id) -> dict[tuple, dict]:
    """{(sid, review_type): {state, ...}} for every question with votes or a resolution."""
    keys = {(v["subject"], v["review_type"]) for v in _votes(conn, project_id)}
    res = _resolutions(conn, project_id)
    return {k: _state(res.get(k)) for k in keys | set(res)}


def tally(votes: list[dict], members: dict) -> tuple[list[dict], list[int], int, int]:
    """(answers, counted vote ids, total, stale). `votes` oldest first."""
    answers: dict[str, dict] = {}
    counted = []
    for v in votes:
        a = answers.setdefault(v["answer"], {"answer": v["answer"], "count": 0, "stale": 0,
                                             "vote_ids": [], "proposal": None})
        if v["stale"]:
            a["stale"] += 1
        elif v["user_id"] in members:
            a["count"] += 1
            a["vote_ids"].append(v["id"])
            counted.append(v["id"])
            if a["proposal"] is None:
                a["proposal"] = v["proposal"]
    rows = sorted(answers.values(), key=lambda a: (-a["count"], a["answer"]))
    return rows, counted, sum(a["count"] for a in rows), sum(a["stale"] for a in rows)


def _question(key, votes, res, members, me_id) -> dict:
    answers, _, total, stale = tally(votes, members)
    mine = next((v for v in votes if v["user_id"] == me_id), None)
    return {"sid": key[0], "review_type": key[1], **_state(res), "answers": answers,
            "total": total, "stale": stale,
            "mine": None if mine is None else {
                "vote_id": mine["id"], "answer": mine["answer"], "proposal": mine["proposal"],
                "comment": mine["comment"], "stale": mine["stale"]}}


def _questions(conn, project_id, me_id, sid=None, review_type=None) -> list[dict]:
    votes = _votes(conn, project_id, sid=sid, review_type=review_type)
    res = _resolutions(conn, project_id, sid=sid, review_type=review_type)
    members = roles.active_members(conn, project_id)
    by_key: dict[tuple, list] = {}
    for v in votes:
        by_key.setdefault((v["subject"], v["review_type"]), []).append(v)
    keys = sorted(set(by_key) | set(res))
    return [_question(k, by_key.get(k, []), res.get(k), members, me_id) for k in keys]


def subject_votes(conn, project_id, sid) -> list[dict]:
    """Current votes on one subject, every review type (the subject page)."""
    votes = _votes(conn, project_id, sid=sid)
    members = roles.active_members(conn, project_id)
    names = _names(conn, [v["user_id"] for v in votes])
    return [vote_out(v, names, members) for v in votes]


# ------------------------------------------------------------------ reads

def questions(engine, caller: Caller, slug: str, review_type=None, state=None, sid=None) -> dict:
    if state not in (None, "", "open", "resolved"):
        raise BadRequest("state must be open or resolved")
    with engine.connect() as conn:
        project, _ = roles.access(conn, caller, slug)
        rows = _questions(conn, project["id"], caller.user.id, sid=sid or None,
                          review_type=review_type or None)
    if state:
        rows = [q for q in rows if q["state"] == state]
    return {"questions": rows}


def votes(engine, caller: Caller, slug: str, sid: str, review_type: str) -> dict:
    with engine.connect() as conn:
        project, _ = roles.access(conn, caller, slug)
        found = _votes(conn, project["id"], sid=sid, review_type=review_type)
        members = roles.active_members(conn, project["id"])
        names = _names(conn, [v["user_id"] for v in found])
    return {"votes": [vote_out(v, names, members) for v in found]}


# ------------------------------------------------------------------ writes

def _lock_project(conn, project_id):
    conn.execute(select(projects.c.id).where(projects.c.id == project_id).with_for_update())


def _require_open(conn, project_id, sid, review_type):
    res = _resolutions(conn, project_id, sid=sid, review_type=review_type).get((sid, review_type))
    if _state(res)["state"] == "resolved":
        raise QuestionResolved(f"the question on {sid} ({review_type}) is resolved; "
                               "a curator must reopen it first")


def _str(body, key, required=True):
    v = body.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v:
        raise BadRequest(f"{key} is required")
    return v


def cast(engine, caller: Caller, slug: str, body) -> dict:
    """Upsert the caller's one vote on a question (voter + `curation:vote`)."""
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    with engine.connect() as conn:
        project, _ = roles.access(conn, caller, slug, need="voter")
        pd = project_data.load_for(conn, project)
    vote = {("subject" if k == "sid" else k): v for k, v in body.items()
            if k != "schema_fingerprint"}
    vote["schema_fingerprint"] = pd.subjects.fingerprint
    store = PgStore(engine, project["id"])
    with store.transaction() as conn:
        _lock_project(conn, project["id"])
        if isinstance(vote.get("subject"), str) and isinstance(vote.get("review_type"), str):
            _require_open(conn, project["id"], vote["subject"], vote["review_type"])
        v = store.cast_vote(vote, project_data.context(pd), user_id=caller.user.id)
        refresh_stale(conn, project["id"])
        members = roles.active_members(conn, project["id"])
        names = _names(conn, [v["user_id"]])
    return {"vote": vote_out(v, names, members)}


def retract(engine, caller: Caller, slug: str, sid: str, review_type: str) -> None:
    with engine.connect() as conn:
        project, _ = roles.access(conn, caller, slug, need="voter")
    store = PgStore(engine, project["id"])
    with store.transaction() as conn:
        _lock_project(conn, project["id"])
        _require_open(conn, project["id"], sid, review_type)
        store.retract_vote(sid, review_type, user_id=caller.user.id)
        refresh_stale(conn, project["id"])


def _check_question(pd, sid, review_type):
    if pd.subjects.get(sid) is None:
        raise StoreError(400, [f"unknown subject {sid!r}"])
    if review_type != "subject" and _review_type_kinds(pd.bundle.get("review_types"),
                                                        review_type) is None:
        raise StoreError(400, [f"unknown review type {review_type!r}"])


def tally_evidence(answers, stale) -> dict:
    parts = ", ".join(f"{a['answer']} {a['count']}" for a in answers if a["count"])
    detail = f"votes: {parts or 'none counted'}"
    if stale:
        detail += f" ({stale} stale not counted)"
    return {"kind": "other", "detail": detail}


def _record_from_vote(conn, project_id, pd, vote_id, sid, review_type) -> dict:
    found = [v for v in _votes(conn, project_id, sid=sid, review_type=review_type)
             if v["id"] == vote_id]
    if not found:
        raise StoreError(400, [f"vote {vote_id} is not a vote on {sid} ({review_type})"])
    v = found[0]
    if not v["proposal"]:
        raise StoreError(400, [f"vote {vote_id} has no proposal to turn into a decision"])
    rec = {k: val for k, val in v["proposal"].items()}
    rec["subject"] = sid
    hashes = {sid: v["subject_hash"]}
    for other in rec.get("also_affects") or []:
        cur = pd.subjects.get(other)
        if cur is not None:
            hashes[other] = cur["hash"]
    rec["subject_hashes"] = hashes
    return rec


def _question_out(conn, project_id, me_id, sid, review_type) -> dict:
    rows = _questions(conn, project_id, me_id, sid=sid, review_type=review_type)
    return rows[0]


def resolve(engine, caller: Caller, slug: str, body) -> dict:
    """Close a question (curator + `curation:decide`): with `no_action`, or
    with a decision built from `from_vote` (that vote's proposal) or from
    `decision` (a full client record). The decision links every counted vote
    and carries the tally as evidence. All or nothing."""
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    sid, review_type = _str(body, "sid"), _str(body, "review_type")
    outcome = body.get("outcome")
    if outcome not in OUTCOMES:
        raise BadRequest(f"outcome must be one of {list(OUTCOMES)}")
    note = body.get("note") or ""
    if not isinstance(note, str):
        raise BadRequest("note must be a string")
    from_vote, client = body.get("from_vote"), body.get("decision")
    if outcome == "decision":
        if (from_vote is None) == (client is None):
            raise BadRequest("a decision outcome needs exactly one of from_vote or decision")
        if from_vote is not None and (not isinstance(from_vote, int) or isinstance(from_vote, bool)):
            raise BadRequest("from_vote must be a vote id")
    elif from_vote is not None or client is not None:
        raise BadRequest("no_action takes no from_vote or decision")
    with engine.connect() as conn:
        project, _ = roles.access(conn, caller, slug, need="curator")
        pd = project_data.load_for(conn, project)
    _check_question(pd, sid, review_type)
    pid = project["id"]
    store = PgStore(engine, pid)
    out = None
    with store.transaction() as conn:
        _lock_project(conn, pid)
        _require_open(conn, pid, sid, review_type)
        decision_row = None
        if outcome == "decision":
            if from_vote is not None:
                rec = _record_from_vote(conn, pid, pd, from_vote, sid, review_type)
            else:
                rec = client_record(client)
                rec.setdefault("subject", sid)
                if rec["subject"] != sid:
                    raise StoreError(400, [f"the decision is about {rec['subject']}, "
                                           f"not the question's subject {sid}"])
            members = roles.active_members(conn, pid)
            answers, counted, _total, stale = tally(
                _votes(conn, pid, sid=sid, review_type=review_type), members)
            evidence = rec.get("evidence")
            rec["evidence"] = [*(evidence if isinstance(evidence, list) else []),
                               tally_evidence(answers, stale)]
            rec.update(decided_by=decided_by(caller), schema_fingerprint=pd.subjects.fingerprint,
                       status="pending")
            rec.pop("decided_on", None)
            out = store.create(rec, project_data.context(pd), vote_ids=counted,
                               decided_by_user_id=caller.user.id)
            decision_row = conn.execute(select(decisions.c.id).where(
                decisions.c.project_id == pid, decisions.c.dec_id == out["id"])).scalar_one()
        conn.execute(question_resolutions.insert().values(
            project_id=pid, subject=sid, review_type=review_type, state="resolved",
            outcome=outcome, decision_id=decision_row, resolved_by=caller.user.id, note=note))
        refresh_stale(conn, pid)
        question = _question_out(conn, pid, caller.user.id, sid, review_type)
    return {"question": question, "decision": out}


def reopen(engine, caller: Caller, slug: str, body) -> dict:
    """Reopen a resolved question (curator). One resolved with a decision
    reopens only after that decision is withdrawn."""
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    sid, review_type = _str(body, "sid"), _str(body, "review_type")
    with engine.connect() as conn:
        project, _ = roles.access(conn, caller, slug, need="curator")
    pid = project["id"]
    with engine.begin() as conn:
        _lock_project(conn, pid)
        res = _resolutions(conn, pid, sid=sid, review_type=review_type).get((sid, review_type))
        if _state(res)["state"] != "resolved":
            raise Conflict(f"the question on {sid} ({review_type}) is not resolved")
        if res["outcome"] == "decision" and res["decision_status"] != "withdrawn":
            raise DecisionStillActive(f"{res['dec_id']} is {res['decision_status']}; "
                                      "withdraw it before reopening the question")
        conn.execute(question_resolutions.insert().values(
            project_id=pid, subject=sid, review_type=review_type, state="reopened",
            outcome=None, decision_id=None, resolved_by=caller.user.id, note=body.get("note") or ""))
        refresh_stale(conn, pid)
        question = _question_out(conn, pid, caller.user.id, sid, review_type)
    return {"question": question}


__all__ = ["cast", "question_states", "questions", "reopen", "resolve", "retract",
           "subject_votes", "tally", "tally_evidence", "vote_out", "votes"]
