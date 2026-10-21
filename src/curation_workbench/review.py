"""Review tracks and types: the queue split into kinds of work.

`review_types.yaml` version 2 (see adapters/onga/review_types.yaml) declares
tracks (by subject kind) and, inside each, types (by finding rule), plus the
project's display `title`, plain-language `reasons` per finding rule, per
`answers_kind` settings (`answers`) and extra menu links (`more_links`).
`derive.review(bundle, status, decisions_next_id)` sorts every queue item into
them and builds each item's answers (answers.py); `build(wb)` is the
local-mode wrapper over snapshot + derive. review.json (version 2):

  {_generated_by, version: 2, title, schema_fingerprint, decisions_next_id,
   progress: {waiting, percent_settled}, more_links: [{label, url}],
   tracks: [{id, title, blurb, kinds, total,
     types: [{id, title, question, answers, help, answers_kind, count,
              items: [item (answers.py)]}]}]}

An item's open findings are its status.json `open_findings`, minus any
suggestion whose object already has an SSSOM row on the term (compared as a
CURIE, so an IRI-form object matches its CURIE row).
"""
import json

import yaml

GENERATED_BY = "cwb review; do not edit"
VALID_KEYS = {"id", "title", "question", "answers", "help", "rules", "answers_kind", "fallback"}
TOP_KEYS = {"version", "title", "tracks", "reasons", "answers", "more_links"}
REASON_KEYS = {"pattern", "text", "else", "hide", "hide_if"}
ANSWERS_KINDS = {"signoff", "links", "definitions", "duplicates", "structure", "keep-or-cut", "fields"}


class ReviewTypesError(Exception):
    pass


def load(wb):
    path = wb.cfg.review_types
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text()) or {}
    return validate(data, path.name)


def validate(data, name="review_types.yaml"):
    if data.get("version") != 2:
        raise ReviewTypesError(
            f"{name} must say `version: 2` (version 2 adds `title`, `reasons`, `answers` "
            "and `more_links`; see the README)")
    extra = set(data) - TOP_KEYS
    if extra:
        raise ReviewTypesError(f"{name}: unknown top-level keys {sorted(extra)}")
    for rule, r in (data.get("reasons") or {}).items():
        bad = set(r or {}) - REASON_KEYS
        if bad:
            raise ReviewTypesError(f"{name}: reason {rule}: unknown keys {sorted(bad)}")
    for link in data.get("more_links") or []:
        if not isinstance(link, dict) or set(link) != {"label", "url"}:
            raise ReviewTypesError(f"{name}: each more_links entry needs exactly label and url")
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
            kind = ty.get("answers_kind")
            if kind is not None and kind not in ANSWERS_KINDS:
                raise ReviewTypesError(f"type {t['id']}/{ty['id']}: unknown answers_kind {kind!r}")
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
    return derive.review(bundle, status, next_id(wb.store))


def next_id(store):
    """The id the store mints next (`DEC-0160`, or `H-0012` for a hosted store):
    review.json's `decisions_next_id`."""
    from .store import YamlStore
    n = int(store.snapshot().get("next_id") or 1)
    return f"{'DEC' if isinstance(store, YamlStore) else 'H'}-{n:04d}"


def dump(data):
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def summary(data):
    return "; ".join(f"{t['id']} {t['total']}: " + " ".join(f"{ty['id']}={ty['count']}" for ty in t["types"])
                     for t in data["tracks"])


def render(wb):
    data = build(wb)
    return dump(data), summary(data)
