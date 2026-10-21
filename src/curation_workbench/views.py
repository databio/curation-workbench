"""Read-route bodies for the review UI, built from plain data.

Every function takes plain data (a snapshot bundle, status.json and review.json
data, decision records) and never a Workbench or a file path, so the hosted
API (hosted/service/data.py) runs no plugin code and the tests can call them
directly. `records` is the decision list: `derive.merge_decisions(repo, hosted)`.

  review_index(review)                         GET /api/review
  review_type_page(review, track, type)        GET /api/review/<track>/<type>  (None: 404)
  subject_index(bundle, status)                GET /api/subject_index
  subject_view(bundle, status, records, sid)   GET /api/subject?sid=           (None: 404)
  verdicts_view(verdicts)                      GET /api/verdicts
  findings_view(bundle, status)                GET /api/findings
  history(records, bundle)                     GET /api/history
  pending_view(records, effects=, errors=, files=)  GET /api/pending
"""
from . import answers
from . import findings as findings_mod
from .status import resolve


def _with_text(found, bundle, sid=None):
    """Each finding plus `text`, the plain words the review rows show for it
    (review_types.yaml `reasons`), or its raw message when the rule has none
    or hides it."""
    ctx = answers.Ctx(bundle)
    out = []
    for f in found:
        who = sid or next(iter(f.get("subjects") or []), None)
        out.append({**f, "text": answers.reason(f, who, ctx) or f["message"]})
    return out


def _aliases(bundle):
    return {k: v for k, v in (bundle.get("aliases") or {}).items() if not k.startswith("_")}


def _name(s):
    return (s.get("label") or s["name"]) if s["kind"] == "term" else s["name"]


def _id_key(rec_id):
    """Sort key for decision ids: by prefix, then number."""
    prefix, _, num = str(rec_id).rpartition("-")
    return (prefix, int(num) if num.isdigit() else -1)


def review_index(review):
    """review.json without any items (the review home page); each type keeps
    only `sids`, its items' SIDs in queue order (the All items page)."""
    if review is None:
        return None
    return {**review, "tracks": [
        {**t, "types": [{**{k: v for k, v in ty.items() if k != "items"},
                         "sids": [i["sid"] for i in ty["items"]]} for ty in t["types"]]}
        for t in review["tracks"]]}


def review_type_page(review, track_id, type_id):
    if review is None:
        return None
    track = next((t for t in review["tracks"] if t["id"] == track_id), None)
    ty = next((y for y in (track or {}).get("types") or [] if y["id"] == type_id), None)
    if ty is None:
        return None
    return {"track": {k: v for k, v in track.items() if k != "types"},
            "type": {k: v for k, v in ty.items() if k != "items"},
            "title": review.get("title"),
            "decisions_next_id": review.get("decisions_next_id"),
            "items": ty["items"]}


def subject_index(bundle, status):
    """{sid: compact row} for names, links, state chips and SID pickers."""
    st = status["subjects"]
    out = {}
    for sid, s in bundle["subjects"]["subjects"].items():
        row = st.get(sid) or {}
        out[sid] = {"kind": s["kind"], "name": s["name"], "label": s.get("label") or s["name"],
                    "slug": s.get("slug"), "hash": s["hash"], "container": s.get("container"),
                    "layer": s.get("layer"), "retired": bool(s.get("retired")),
                    "state": row.get("state", "unreviewed"), "score": row.get("score", 0)}
    return out


def _touches(rec, sids):
    return rec["subject"] in sids or any(s in sids for s in rec.get("also_affects") or []) \
        or any(s in sids for s in (rec.get("applied") or {}).get("created") or [])


def subject_view(bundle, status, records, sid):
    """Everything the subject page shows, or None for an unknown SID."""
    subjects = bundle["subjects"]["subjects"]
    rec = subjects.get(sid)
    if rec is None:
        return None
    aliases = _aliases(bundle)
    forward = resolve(sid, aliases) if sid in aliases else None
    former = sorted(k for k in aliases if k != sid and resolve(k, aliases) == sid)
    found = findings_mod.collect(bundle, status)
    mine = [f for f in found.values()
            if sid in {resolve(s, aliases) for s in f.get("subjects") or []}]
    history = sorted((d for d in records if _touches(d, {sid, *former})),
                     key=lambda d: _id_key(d["id"]), reverse=True)
    row = status["subjects"].get(sid) or {}
    return {
        "subject": rec,
        "status": {"state": row.get("state", "unreviewed"), "score": row.get("score", 0),
                   "open_findings": row.get("open_findings") or [],
                   "latest": row.get("latest")},
        "findings": _with_text(mine, bundle, sid),
        "decisions": history,
        "aliases": {"forward": forward, "former": former},
        "prompts": (bundle.get("prompts") or {}).get(sid) or [],
        "panels": (bundle.get("panels") or {}).get(sid) or [],
        "browse": (bundle.get("browse") or {}).get(sid),
    }


def verdicts_view(verdicts):
    """{kind: {verdict: entry}} without seed-only verdicts."""
    return {k: {v: e for v, e in vs.items() if not (e or {}).get("seed_only")}
            for k, vs in (verdicts or {}).items()}


def findings_view(bundle, status):
    return {"findings": _with_text(findings_mod.collect(bundle, status).values(), bundle)}


def history(records, bundle):
    """Every decision, newest first, each with where its subject lives now."""
    subjects = bundle["subjects"]["subjects"]
    aliases = _aliases(bundle)
    rows = []
    for d in sorted(records, key=lambda d: _id_key(d["id"]), reverse=True):
        now = resolve(d["subject"], aliases)
        s = subjects.get(now)
        rows.append({**d, "now": now if now != d["subject"] else None,
                     "name": _name(s) if s else d["subject"],
                     "kind": s["kind"] if s else d["subject"].split(":", 1)[0],
                     "layer": s.get("layer") if s else None,
                     "slug": s.get("slug") if s else None})
    return {"history": rows}


def pending_view(records, *, effects=None, errors=None, files=None):
    """Pending decisions with what applying them would do. `effects`
    {id: {created, retired, renamed}}, `errors` {id: [str]} and `files`
    [{path, diff}] come from an apply dry run (the hosted API passes none:
    it never runs the apply engine)."""
    effects = effects or {}
    rows = [{**d, "effects": effects.get(d["id"])} for d in records if d.get("status") == "pending"]
    return {"decisions": rows, "errors": errors or {}, "files": files,
            "ok": not errors}
