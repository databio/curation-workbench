"""GitHub push webhook: `handle(conn, slug, headers, body) -> (status, body)`.

Plan 2 mounts it at `POST /api/v1/hooks/github/{slug}`. It checks
`X-Hub-Signature-256` against the resident's secret (`hmac.compare_digest`),
answers `ping` with 200, ignores pushes to other branches, and otherwise
queues a `commit` job (trigger `webhook`) and answers 202. It never runs git
and never trusts the payload's commit: the worker fetches and resolves the
branch head itself. A per-resident limit (30 requests a minute) answers 429.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from collections import defaultdict, deque

from sqlalchemy import select

from ...store.pg.tables import projects
from . import jobs
from .tables import cw_residents

RATE_LIMIT = 30
RATE_WINDOW = 60.0
_hits = defaultdict(deque)
_lock = threading.Lock()


def _limited(resident_id, now=None):
    now = time.monotonic() if now is None else now
    with _lock:
        q = _hits[resident_id]
        while q and now - q[0] > RATE_WINDOW:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            return True
        q.append(now)
        return False


def sign(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def handle(conn, slug, headers, body):
    """`headers` is any mapping (case-insensitive lookup is done here); `body`
    is the raw request bytes. Returns (HTTP status, JSON-able body)."""
    h = {str(k).lower(): v for k, v in dict(headers).items()}
    row = conn.execute(select(cw_residents.c.id, cw_residents.c.branch, cw_residents.c.enabled,
                              cw_residents.c.webhook_secret)
                       .join(projects, projects.c.id == cw_residents.c.project_id)
                       .where(projects.c.slug == slug)).one_or_none()
    if row is None or not row.enabled or not row.webhook_secret:
        return 404, {"error": f"no webhook for {slug}"}
    if _limited(row.id):
        return 429, {"error": "too many webhook requests; slow down"}
    given = h.get("x-hub-signature-256") or ""
    if not hmac.compare_digest(given.encode(), sign(row.webhook_secret, body).encode()):
        return 401, {"error": "bad signature"}
    event = h.get("x-github-event")
    if event == "ping":
        return 200, {"ok": True}
    if event != "push":
        return 400, {"error": f"only push events are accepted, got {event!r}"}
    try:
        payload = json.loads(body)
    except ValueError:
        return 400, {"error": "body is not JSON"}
    ref = payload.get("ref") if isinstance(payload, dict) else None
    if ref != f"refs/heads/{row.branch}":
        return 202, {"queued": False, "ignored": f"push to {ref}, not {row.branch}"}
    job = jobs.enqueue(conn, row.id, "commit", "webhook")
    return 202, {"queued": job is not None}
