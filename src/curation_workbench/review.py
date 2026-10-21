"""Review tracks and types: the queue split into kinds of work.

`review_types.yaml` (see adapters/onga/review_types.yaml for the format)
declares tracks (by subject kind) and, inside each, types (by finding rule).
`derive.review(bundle, status)` sorts every queue item into them (`build(wb)`
is the local-mode wrapper over snapshot + derive) and returns review.json:

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
    """review.json data for the workbench's project and its own decision store."""
    if load(wb) is None:
        raise ReviewTypesError(f"{wb.cfg.rel(wb.cfg.review_types)} does not exist")
    from . import derive, snapshot
    bundle = snapshot.build(wb)
    status = derive.status(bundle, wb.store.records())
    return derive.review(bundle, status, wb.store.snapshot().get("next_id"))


def dump(data):
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def summary(data):
    return "; ".join(f"{t['id']} {t['total']}: " + " ".join(f"{ty['id']}={ty['count']}" for ty in t["types"])
                     for t in data["tracks"])


def render(wb):
    data = build(wb)
    return dump(data), summary(data)
