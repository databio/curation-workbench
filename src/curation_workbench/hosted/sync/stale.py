"""Staleness rules: which open votes and pending hosted decisions no longer
match the subject they were made about.

Run against a snapshot bundle's `subjects` and `aliases` (alias chains
followed with `status.resolve`) over the store's `open_votes()` and
`pending_decisions()`:

- a SID that resolves to nothing, or to a retired subject: `subject_removed`
- a SID that was renamed (resolves to another SID): `renamed`, with `new_sid`
- a hash that differs from the subject's current hash: `subject_changed`
- otherwise a mark, if there was one, is cleared (a revert brought it back)

A pending decision is tested on every `subject_hashes` entry; the first
failing one gives the reason. Applied and withdrawn decisions and the repo's
own decisions are never touched. Every mark and clear writes one
`cw_stale_events` row; nothing is ever deleted. Marks are written through the
store (`set_stale` / `clear_stale`), which never bumps `decisions_rev`.

The pass is idempotent (it writes only when a row's mark changes), so it runs
in full on every publish and after every vote or decision write.
"""
from __future__ import annotations

from ...status import resolve
from .tables import cw_stale_events


def judge(sid, recorded_hash, subjects, aliases):
    """(reason, new_sid) for a stale reference, or None when it still matches."""
    target = resolve(sid, aliases)
    s = subjects.get(target)
    if s is None or s.get("retired"):
        return "subject_removed", None
    if target != sid:
        return "renamed", target
    if recorded_hash is not None and s.get("hash") != recorded_hash:
        return "subject_changed", None
    return None


def _decision_verdict(hashes, subjects, aliases):
    for sid, h in (hashes or {}).items():
        v = judge(sid, h, subjects, aliases)
        if v is not None:
            return sid, v
    return None, None


def run_pass(store, conn, project_id, snapshot_id, bundle):
    """Mark and clear stale votes and pending decisions against `bundle`.
    `store` is a PgStore bound to `conn` (its `using(conn)` or `transaction()`).
    Returns {"marked": n, "cleared": n}."""
    subjects = bundle["subjects"]["subjects"]
    aliases = {k: v for k, v in (bundle.get("aliases") or {}).items() if not k.startswith("_")}
    counts = {"marked": 0, "cleared": 0}
    events = []

    def apply(kind, row, sid, verdict):
        if verdict is not None:
            reason, new_sid = verdict
            if row["stale"] and (row["stale_reason"], row["stale_new_sid"]) == (reason, new_sid):
                return
            store.set_stale(kind, row["id"], snapshot_id, reason, new_sid)
            events.append({"object_kind": kind, "object_id": row["id"], "action": "marked",
                           "sid": sid, "reason": reason, "new_sid": new_sid})
            counts["marked"] += 1
        elif row["stale"]:
            store.clear_stale(kind, row["id"])
            events.append({"object_kind": kind, "object_id": row["id"], "action": "cleared",
                           "sid": sid, "reason": row["stale_reason"], "new_sid": None})
            counts["cleared"] += 1

    for v in store.open_votes():
        apply("vote", v, v["subject"], judge(v["subject"], v["subject_hash"], subjects, aliases))
    for d in store.pending_decisions():
        sid, verdict = _decision_verdict(d["subject_hashes"], subjects, aliases)
        apply("decision", d, sid, verdict)
    if events:
        conn.execute(cw_stale_events.insert(), [
            {"project_id": project_id, "snapshot_id": snapshot_id, **e} for e in events])
    return counts
