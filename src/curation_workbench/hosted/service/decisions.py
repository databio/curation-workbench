"""Hosted decisions: read (any member), write (curator + `curation:decide`).

The server owns the same fields as the local daemon (`SERVER_FIELDS`); the
client cannot set them. It sets `decided_by` to "Name <email>",
`decided_by_user_id`, `decided_on` today, `schema_fingerprint` from the
current snapshot and `status: pending`; `PgStore.create` mints the `H-` id
and sets `origin: {kind: hosted, ref: <id>}`. Updates and withdrawals need
`If-Match: <rev>` (else 428). Every write asks plan 4's worker to re-derive.
"""
from __future__ import annotations

import datetime

from snapgate import Caller

from ...store.pg import PgStore
from ...store.pg.store import INTERNAL_KEYS
from ..sync import request_derive
from .. import project_data
from . import roles
from .data import with_plugin_errors
from .errors import BadRequest, NotSynced, PreconditionRequired

#: The same server-owned fields as the local daemon's `server.SERVER_FIELDS`
#: (tests/hosted/test_decisions_api.py checks they agree).
SERVER_FIELDS = ("id", "decided_by", "decided_on", "schema_fingerprint", "status", "applied", "origin")
#: Keys a client may send back from a read that are never stored.
READ_ONLY_KEYS = (*INTERNAL_KEYS, "plugin_errors")


def decided_by(caller: Caller) -> str:
    return f"{caller.user.name} <{caller.user.email}>"


def client_record(body) -> dict:
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    return {k: v for k, v in body.items() if k not in SERVER_FIELDS and k not in READ_ONLY_KEYS}


def server_fields(rec: dict, caller: Caller, pd) -> dict:
    rec.update(decided_by=decided_by(caller), decided_on=datetime.date.today().isoformat(),
               schema_fingerprint=pd.subjects.fingerprint, status="pending")
    return rec


def parse_if_match(value) -> int:
    if value is None or not str(value).strip():
        raise PreconditionRequired("send If-Match: <rev> (the decision's rev when you read it)")
    raw = str(value).strip()
    if raw.startswith("W/"):
        raw = raw[2:]
    raw = raw.strip('"')
    try:
        return int(raw)
    except ValueError:
        raise BadRequest(f"If-Match must be the decision's rev (an integer), got {value!r}") from None


def _checks(conn, project):
    try:
        return project_data.load_for(conn, project).decision_checks
    except NotSynced:
        return {}


def list_decisions(engine, caller: Caller, slug: str, status: str | None = None) -> dict:
    with engine.connect() as conn:
        project, _ = roles.access(conn, caller, slug)
        checks = _checks(conn, project)
        store = PgStore(engine, project["id"])
        with store.using(conn):
            snap = store.snapshot()
    recs = with_plugin_errors(snap["decisions"], checks)
    if status:
        recs = [r for r in recs if r.get("status") == status]
    return {"version": snap["version"], "next_id": f"H-{int(snap['next_id']):04d}",
            "decisions": recs, "token": snap["token"]}


def get_decision(engine, caller: Caller, slug: str, dec_id: str) -> dict:
    with engine.connect() as conn:
        project, _ = roles.access(conn, caller, slug)
        checks = _checks(conn, project)
        store = PgStore(engine, project["id"])
        with store.using(conn):
            rec = store.get(dec_id)
    return {"decision": with_plugin_errors([rec], checks)[0]}


def _for_write(engine, caller, slug):
    with engine.connect() as conn:
        project, _ = roles.access(conn, caller, slug, need="curator")
        pd = project_data.load_for(conn, project)
    return project, pd


def create(engine, caller: Caller, slug: str, body) -> dict:
    rec = client_record(body)
    project, pd = _for_write(engine, caller, slug)
    server_fields(rec, caller, pd)
    store = PgStore(engine, project["id"])
    with store.transaction() as conn:
        out = store.create(rec, project_data.context(pd), decided_by_user_id=caller.user.id)
        request_derive(conn, project["id"])
    return {"decision": with_plugin_errors([out], {})[0]}


def update(engine, caller: Caller, slug: str, dec_id: str, body, if_match) -> dict:
    rev = parse_if_match(if_match)
    rec = client_record(body)
    project, pd = _for_write(engine, caller, slug)
    server_fields(rec, caller, pd)
    rec["id"] = dec_id
    store = PgStore(engine, project["id"])
    with store.transaction() as conn:
        out = store.update(dec_id, rec, project_data.context(pd), expect_rev=rev,
                           decided_by_user_id=caller.user.id)
        request_derive(conn, project["id"])
    return {"decision": with_plugin_errors([out], pd.decision_checks)[0]}


def withdraw(engine, caller: Caller, slug: str, dec_id: str, if_match) -> dict:
    rev = parse_if_match(if_match)
    with engine.connect() as conn:
        project, _ = roles.access(conn, caller, slug, need="curator")
        checks = _checks(conn, project)
    store = PgStore(engine, project["id"])
    with store.transaction() as conn:
        out = store.withdraw(dec_id, expect_rev=rev)
        request_derive(conn, project["id"])
    return {"decision": with_plugin_errors([out], checks)[0]}


def export(engine, caller: Caller, slug: str) -> dict:
    """Pending hosted decisions as plain records (no rev, vote links or stale
    marks; dates as YYYY-MM-DD). Renumbering into the repo's DEC- sequence is
    `store.pg.export_yaml`, not this."""
    with engine.connect() as conn:
        project, _ = roles.access(conn, caller, slug)
        store = PgStore(engine, project["id"])
        with store.using(conn):
            recs = store.records()
    return {"decisions": [{k: v for k, v in r.items() if k not in READ_ONLY_KEYS}
                          for r in recs if r.get("status") == "pending"]}


__all__ = ["SERVER_FIELDS", "client_record", "create", "decided_by", "export", "get_decision",
           "list_decisions", "parse_if_match", "server_fields", "update", "withdraw"]
