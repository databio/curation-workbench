"""Curation status: a state and a queue score for every subject.

Reads subjects.json, decisions.yaml, subject_aliases.json, the findings files
(`<out>/findings/*.json`) plus any findings a plugin supplies, and policy.yaml
(`queue:` weights, `status:` severities). Writes `status.json`:

  {_generated_by, schema_fingerprint, weights,
   sources:  [{path, findings, stale}],
   subjects: {sid: {kind, container, layer, retired, state, open_findings,
                    decisions, latest, signals, score}},
   rollups:  {overall, by_kind, by_layer, by_container},
   queue:    [sid ...]}                          # open | stale | unreviewed, score desc

States: unreviewed (no decision, no open finding), open (an open finding, with
or without earlier decisions), deferred (newest decision is `defer`), settled
(terminal, not stale), applied (settled and `status: applied`), stale (the
newest terminal decision's recorded hash differs from the current one).

A finding is open for a subject unless a live terminal decision cites it in its
evidence, or a current terminal decision on the subject cites no finding at all
(a blanket judgment). A finding with an `object` is also closed once the term
has an SSSOM row for that object. Findings files whose `schema_fingerprint`
differs from subjects.json count as stale and contribute nothing.

Signals: one per `queue:` weight, in the weights' order. `findings` and
`stale` come from the state computation; every other name is looked up in
`wb.signals` (builtin: impact, adoption, required, port, machine) and is 0 when
nothing provides it. score = sum(weight * signal).
"""
import json
from collections import defaultdict
from pathlib import Path

import yaml

from .store import load_verdicts

STATES = ("unreviewed", "open", "deferred", "settled", "applied", "stale")
QUEUE_STATES = ("open", "stale", "unreviewed")
GENERATED_BY = "cwb status; do not edit"


def load_yaml(path, default=None):
    if not Path(path).exists():
        return default
    return yaml.safe_load(Path(path).read_text()) or default


def resolve(sid, aliases):
    seen = set()
    while sid in aliases and sid not in seen:
        seen.add(sid)
        sid = aliases[sid]
    return sid


def load_findings(wb, fingerprint, severity_of):
    """([finding], [source summary]) from the findings dir and every plugin source."""
    findings, sources = [], []
    for path in sorted(wb.cfg.findings_dir.glob("*.json")):
        data = json.loads(path.read_text())
        stale = data.get("schema_fingerprint") != fingerprint
        items = [] if stale else data.get("findings") or []
        sources.append({"path": wb.cfg.rel(path), "findings": len(items), "stale": stale})
        findings += [{**f, "source": path.stem} for f in items]
    for fn in wb.findings_sources:
        f, s = fn(wb, fingerprint, severity_of)
        findings += f
        sources += s
    return findings, sources


class Context:
    """What a signal function sees. `last_terminal[sid]` is the newest live
    terminal decision on the subject (or None)."""

    def __init__(self, wb, subjects, labels, last_terminal):
        self.wb = wb
        self.subjects = subjects
        self.labels = labels
        self.last_terminal = last_terminal


def build(wb):
    cfg = wb.cfg
    reg = json.loads(cfg.output("subjects.json").read_text())
    fingerprint, subjects = reg["schema_fingerprint"], reg["subjects"]
    policy = load_yaml(cfg.policy)
    w, spol = policy["queue"], policy["status"]
    points = spol["severity_points"]
    terminal = {(k, v): e["terminal"] for k, vs in load_verdicts(cfg.verdicts).items()
                for v, e in vs.items()}
    apath = cfg.output("subject_aliases.json")
    aliases = json.loads(apath.read_text()) if apath.exists() else {}
    aliases = {k: v for k, v in aliases.items() if not k.startswith("_")}
    xpath = cfg.output("term_crosswalk.json")
    labels = json.loads(xpath.read_text())["labels"] if xpath.exists() else {}

    findings, sources = load_findings(wb, fingerprint, spol.get("finding_severity") or {})

    # Decisions per subject, oldest first.
    decs = wb.store.records()
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

    last_terminal = {}
    for sid in subjects:
        ts = [d for d in by_subject.get(sid, []) if is_terminal(d, sid)]
        last_terminal[sid] = ts[-1] if ts else None
    ctx = Context(wb, subjects, labels, last_terminal)
    provided = {name: fn(ctx) for name, fn in wb.signals.items() if name in w}

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
    return {"_generated_by": GENERATED_BY, "schema_fingerprint": fingerprint, "weights": w,
            "sources": sources, "subjects": out, "rollups": rollups, "queue": queue}


def render(wb):
    data = build(wb)
    text = json.dumps(data, indent=1, ensure_ascii=False, default=str) + "\n"
    o = data["rollups"]["overall"]
    summary = (f"status: {o['total']} subjects, " + " ".join(f"{s}={o[s]}" for s in STATES)
               + f", {o['percent_settled']}% settled, queue {len(data['queue'])}")
    return text, summary
