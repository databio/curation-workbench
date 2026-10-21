"""The decision phase of regen: status.json and review.json from a snapshot
bundle (snapshot.py) plus a decision list.

Generic, trusted code: it reads only the bundle and the decisions, never
files, and never calls plugin code, so the hosted worker runs it on every
decision change in milliseconds.

  status(bundle, decisions) -> status.json data
  review(bundle, status, decisions_next_id) -> review.json data, or None when
      the project declares no review types
  merge_decisions(repo_records, hosted_records) -> the decision list hosted
      mode derives from (repo history first, then hosted decisions)
"""
from collections import defaultdict

from .review import GENERATED_BY as REVIEW_GENERATED_BY
from .review import dump as dump_review  # noqa: F401  (re-exported)
from .review import to_curie
from .status import GENERATED_BY as STATUS_GENERATED_BY
from .status import QUEUE_STATES, STATES, resolve
from .status import dump as dump_status  # noqa: F401  (re-exported)


def status(bundle, decisions):
    reg = bundle["subjects"]
    fingerprint, subjects = reg["schema_fingerprint"], reg["subjects"]
    policy = bundle["policy"]
    w, spol = policy["queue"], policy["status"]
    points = spol["severity_points"]
    terminal = {(k, v): e["terminal"] for k, vs in bundle["verdicts"].items()
                for v, e in vs.items()}
    aliases = {k: v for k, v in (bundle["aliases"] or {}).items() if not k.startswith("_")}
    findings, sources = bundle["findings"], bundle["sources"]

    # Decisions per subject, oldest first.
    decs = decisions
    by_subject = defaultdict(list)
    for d in decs:
        if d.get("status") == "withdrawn":
            continue
        # A subject an applied decision created (add_term, a rename target)
        # is settled by that decision too.
        created = (d.get("applied") or {}).get("created") or []
        linked = {resolve(sid, aliases) for sid in [d["subject"], *(d.get("also_affects") or []), *created]}
        for sid in linked:
            by_subject[sid].append(d)
    hash_after = {}
    for d in decs:
        if d.get("status") == "applied":
            for k, v in ((d.get("applied") or {}).get("subject_hashes_after") or {}).items():
                hash_after[resolve(k, aliases)] = v
    cited = defaultdict(set)

    def is_terminal(d, sid):
        kind = (subjects.get(resolve(d["subject"], aliases)) or subjects.get(sid) or {}).get("kind")
        return terminal.get((kind, d["verdict"]), d["verdict"] != "defer")

    for sid, ds in by_subject.items():
        for d in ds:
            if is_terminal(d, sid):
                for ev in d.get("evidence") or []:
                    if ev.get("id"):
                        cited[ev["id"]].add(sid)

    findings_of = defaultdict(list)
    for f in findings:
        for sid in f["subjects"]:
            findings_of[resolve(sid, aliases)].append(f)

    has_terminal = {sid for sid in subjects
                    if any(is_terminal(d, sid) for d in by_subject.get(sid, []))}
    provided = dict(bundle["signals"])
    for name, values in bundle["masked_signals"].items():
        provided[name] = {sid: v for sid, v in values.items() if sid not in has_terminal}

    out = {}
    for sid, s in subjects.items():
        ds = by_subject.get(sid, [])
        terms_ = [d for d in ds if is_terminal(d, sid)]
        latest = ds[-1] if ds else None
        last_t = terms_[-1] if terms_ else None
        stale = False
        if last_t is not None:
            if last_t.get("status") == "applied":
                h = hash_after.get(sid)
            else:
                recorded = last_t.get("subject_hashes") or {}
                h = recorded.get(sid)
                if h is None:  # recorded under an alias
                    h = next((v for k, v in recorded.items() if resolve(k, aliases) == sid), None)
            stale = h is not None and h != s["hash"]
        sssom_objects = {r.get("object") for r in s["payload"].get("sssom") or []} \
            if s["kind"] == "term" else set()
        blanket = last_t is not None and not stale and any(
            not any(ev.get("id") for ev in d.get("evidence") or []) for d in terms_)
        open_f = []
        for f in findings_of.get(sid, []):
            if blanket:
                continue
            if sid in cited.get(f["id"], ()):
                continue
            if f.get("object") and f["object"] in sssom_objects:
                continue
            open_f.append(f)
        if latest is not None and latest["verdict"] == "defer":
            state = "deferred"
        elif stale:
            state = "stale"
        elif open_f:
            state = "open"
        elif last_t is not None:
            state = "applied" if last_t.get("status") == "applied" else "settled"
        else:
            state = "unreviewed"
        signals = {}
        for name in w:
            if name == "findings":
                signals[name] = min(10, sum(points[f["severity"]] for f in open_f))
            elif name == "stale":
                signals[name] = int(stale)
            else:
                signals[name] = provided.get(name, {}).get(sid, 0)
        score = round(sum(w[k] * v for k, v in signals.items()), 4)
        out[sid] = {
            "kind": s["kind"], "container": s.get("container"), "layer": s.get("layer"),
            "retired": bool(s.get("retired")), "state": state,
            "open_findings": sorted({f["id"] for f in open_f}),
            "decisions": [d["id"] for d in ds],
            "latest": latest["id"] if latest else None,
            "signals": signals, "score": score,
        }

    live = {sid: r for sid, r in out.items() if not r["retired"]}

    def rollup(rows):
        c = {st: 0 for st in STATES}
        for r in rows:
            c[r["state"]] += 1
        total = sum(c.values())
        done = c["settled"] + c["applied"]
        return {**c, "total": total,
                "percent_settled": round(100 * done / total, 1) if total else 0.0}

    groups = {"by_kind": defaultdict(list), "by_layer": defaultdict(list),
              "by_container": defaultdict(list)}
    for r in live.values():
        groups["by_kind"][r["kind"]].append(r)
        groups["by_layer"][str(r["layer"])].append(r)
        if r["container"]:
            groups["by_container"][r["container"]].append(r)
    rollups = {"overall": rollup(live.values())}
    for name, g in groups.items():
        rollups[name] = {k: rollup(v) for k, v in sorted(g.items())}

    queue = sorted((sid for sid, r in live.items() if r["state"] in QUEUE_STATES),
                   key=lambda sid: (-live[sid]["score"], sid))
    return {"_generated_by": STATUS_GENERATED_BY, "schema_fingerprint": fingerprint, "weights": w,
            "sources": sources, "subjects": out, "rollups": rollups, "queue": queue}


def review(bundle, status, decisions_next_id=None):
    """review.json data: the queue sorted into review tracks and types, or None
    when the bundle has no review types. `decisions_next_id` is reserved for
    the review UI's answer builder (review.json version 2)."""
    data = bundle.get("review_types")
    if data is None:
        return None
    subjects = bundle["subjects"]["subjects"]
    findings = {f["id"]: f for f in bundle["findings"]}
    prefixes = (bundle.get("options") or {}).get("iri_prefixes") or {}

    def open_findings(sid):
        objs = {r.get("object") for r in subjects[sid]["payload"].get("sssom") or []} \
            if subjects[sid]["kind"] == "term" else set()
        out = []
        for fid in status["subjects"][sid]["open_findings"]:
            f = findings.get(fid)
            if f is None:
                continue
            if f.get("object") and to_curie(f["object"], prefixes) in objs:
                continue
            out.append(f)
        return out

    tracks = []
    for t in data["tracks"]:
        types = [{**{k: v for k, v in ty.items() if k not in ("rules", "fallback")},
                  "count": 0, "items": []} for ty in t["types"]]
        total = 0
        for sid in status["queue"]:
            if subjects[sid]["kind"] not in t["kinds"]:
                continue
            total += 1
            found = open_findings(sid)
            rules = {f["rule"] for f in found}
            hits = [i for i, ty in enumerate(t["types"]) if set(ty.get("rules") or []) & rules]
            if not hits:
                hits = [i for i, ty in enumerate(t["types"]) if ty.get("fallback")]
            for i in hits:
                mine = [f["id"] for f in found if f["rule"] in (t["types"][i].get("rules") or [])]
                types[i]["items"].append({"sid": sid, "findings": mine})
                types[i]["count"] += 1
        tracks.append({**{k: v for k, v in t.items() if k != "types"}, "total": total, "types": types})
    return {"_generated_by": REVIEW_GENERATED_BY, "schema_fingerprint": status["schema_fingerprint"],
            "tracks": tracks}


def merge_decisions(repo_records, hosted_records):
    """Repo records first, then hosted ones in creation order, minus any hosted
    record the repo already holds (a repo record with origin {kind: hosted,
    ref: <its H- id>}, as written when hosted decisions land in the repo)."""
    landed = {(r.get("origin") or {}).get("ref") for r in repo_records
              if (r.get("origin") or {}).get("kind") == "hosted"}
    return list(repo_records) + [r for r in hosted_records if r.get("id") not in landed]
