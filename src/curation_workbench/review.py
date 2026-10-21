"""Review tracks and types: the queue split into kinds of work.

`review_types.yaml` (see adapters/onga/review_types.yaml for the format)
declares tracks (by subject kind) and, inside each, types (by finding rule).
`build(wb)` sorts every queue item into them and returns review.json:

  {_generated_by, schema_fingerprint, tracks: [{id, title, blurb, kinds, total,
     types: [{id, title, question, answers, help, answers_kind, count,
              items: [{sid, findings: [finding id of this type]}]}]}]}

An item's open findings are its status.json `open_findings`, minus any
suggestion whose object already has an SSSOM row on the term (compared as a
CURIE, so an IRI-form object matches its CURIE row).
"""
import json

import yaml

GENERATED_BY = "cwb review; do not edit"
VALID_KEYS = {"id", "title", "question", "answers", "help", "rules", "answers_kind", "fallback"}


class ReviewTypesError(Exception):
    pass


def load(wb):
    path = wb.cfg.review_types
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text()) or {}
    seen_kinds = set()
    for t in data.get("tracks") or []:
        for k in t.get("kinds") or []:
            if k in seen_kinds:
                raise ReviewTypesError(f"kind {k} is in more than one track")
            seen_kinds.add(k)
        if sum(1 for ty in t["types"] if ty.get("fallback")) > 1:
            raise ReviewTypesError(f"track {t['id']} has more than one fallback type")
        for ty in t["types"]:
            extra = set(ty) - VALID_KEYS
            if extra:
                raise ReviewTypesError(f"type {t['id']}/{ty['id']}: unknown keys {sorted(extra)}")
    return data


def to_curie(obj, prefixes):
    for base, prefix in prefixes.items():
        if obj.startswith(base):
            return f"{prefix}:{obj[len(base):]}"
    return obj


def build(wb):
    data = load(wb)
    if data is None:
        raise ReviewTypesError(f"{wb.cfg.rel(wb.cfg.review_types)} does not exist")
    cfg = wb.cfg
    status = json.loads(cfg.output("status.json").read_text())
    subjects = json.loads(cfg.output("subjects.json").read_text())["subjects"]
    findings = {}
    lint_path = cfg.findings_dir / "schema_lint.json"
    if lint_path.exists():
        findings.update({f["id"]: f for f in json.loads(lint_path.read_text())["findings"]})
    severity_of = ((yaml.safe_load(cfg.policy.read_text()) or {}).get("status") or {}) \
        .get("finding_severity") or {}
    for fn in wb.findings_sources:
        fs, _ = fn(wb, status["schema_fingerprint"], severity_of)
        findings.update({f["id"]: f for f in fs})
    prefixes = wb.option("iri_prefixes", {}) or {}

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
    return {"_generated_by": GENERATED_BY, "schema_fingerprint": status["schema_fingerprint"],
            "tracks": tracks}


def render(wb):
    data = build(wb)
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    summary = "; ".join(f"{t['id']} {t['total']}: " + " ".join(f"{ty['id']}={ty['count']}" for ty in t["types"])
                        for t in data["tracks"])
    return text, summary

