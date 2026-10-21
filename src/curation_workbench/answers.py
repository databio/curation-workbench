"""Review items: what one row of a review page shows, built in Python so the
review UI only renders data.

  build_items(bundle, status, track_id, type_id) -> [item]
  sort(bundle, status) -> {track_id: {"total": n, "types": {type_id: [(sid, found)]}}}

Pure: reads only a snapshot bundle (snapshot.py) and status.json data, never a
Workbench, files or plugin code, so plan 4's `derive.review` runs it on every
decision change. Project wording comes from the bundle: `review_types`
(`title`, `reasons`, `answers`) and the plugin-filled `suggestions` and
`browse` maps.

An item (review.json version 2):

  {sid, kind, slug, title, container, container_label, browse, description,
   reasons: [str], findings: [finding id], choices: [{text, detail, actions}],
   actions: [Action], editor: {field, value, placeholder} | null,
   group: str | null, hash, score}

  Action = {label, verdict, operation, rationale, evidence, also, tone, input,
            confirm, answer}

`answer` is the vote answer key: the first 12 hex characters of the SHA-1 of
the canonical JSON of {verdict, operation, also, evidence} as built. One
builder per `answers_kind`, ported from the ONGA site's `actionsFor()`.
"""
import hashlib
import json
import re

from .review import to_curie

DEFAULT_GRADE_RATIONALE = "{label} {phrase} this term."


# ---------------------------------------------------------------- membership

def _open_findings(bundle, status, sid, findings, prefixes):
    """The subject's open findings, minus any whose object already has an SSSOM
    row on the term (compared as a CURIE)."""
    s = bundle["subjects"]["subjects"][sid]
    objs = {r.get("object") for r in s["payload"].get("sssom") or []} if s["kind"] == "term" else set()
    out = []
    for fid in status["subjects"][sid]["open_findings"]:
        f = findings.get(fid)
        if f is None:
            continue
        if f.get("object") and to_curie(f["object"], prefixes) in objs:
            continue
        out.append(f)
    return out


def sort(bundle, status):
    """Every queue item sorted into its track and review types, in queue order.
    An item belongs to every type whose `rules` match one of its open findings,
    or to the track's fallback type when none match; `found` is its open
    findings of that type's rules."""
    data = bundle.get("review_types")
    if data is None:
        return None
    subjects = bundle["subjects"]["subjects"]
    findings = {f["id"]: f for f in bundle["findings"]}
    prefixes = (bundle.get("options") or {}).get("iri_prefixes") or {}
    out = {}
    for t in data["tracks"]:
        rows = {ty["id"]: [] for ty in t["types"]}
        total = 0
        for sid in status["queue"]:
            if subjects[sid]["kind"] not in t["kinds"]:
                continue
            total += 1
            found = _open_findings(bundle, status, sid, findings, prefixes)
            rules = {f["rule"] for f in found}
            hits = [ty for ty in t["types"] if set(ty.get("rules") or []) & rules]
            if not hits:
                hits = [ty for ty in t["types"] if ty.get("fallback")]
            for ty in hits:
                mine = [f for f in found if f["rule"] in (ty.get("rules") or [])]
                rows[ty["id"]].append((sid, mine))
        out[t["id"]] = {"total": total, "types": rows}
    return out


# ---------------------------------------------------------------- helpers

def answer_key(verdict, operation, also, evidence):
    blob = json.dumps({"verdict": verdict, "operation": operation, "also": also, "evidence": evidence},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def action(label, verdict, rationale, *, operation=None, evidence=None, also=None,
           tone="neutral", input=None, confirm=None):
    return {"label": label, "verdict": verdict, "operation": operation, "rationale": rationale,
            "evidence": evidence, "also": also, "tone": tone, "input": input, "confirm": confirm,
            "answer": answer_key(verdict, operation, also, evidence)}


class Ctx:
    """What the builders read: subjects, settings and the project's wording."""

    def __init__(self, bundle):
        self.subjects = bundle["subjects"]["subjects"]
        rt = bundle.get("review_types") or {}
        self.project = rt.get("title") or bundle.get("project") or "this project"
        self.reasons = rt.get("reasons") or {}
        self.answers = rt.get("answers") or {}
        self.suggestions = bundle.get("suggestions") or {}
        self.browse = bundle.get("browse") or {}
        self.prefixes = (bundle.get("options") or {}).get("iri_prefixes") or {}
        self.verdicts = bundle.get("verdicts") or {}

    def name(self, sid):
        s = self.subjects.get(sid)
        if not s:
            return sid
        return (s.get("label") or s["name"]) if s["kind"] == "term" else s["name"]

    def text(self, template):
        return template.replace("{project}", self.project)


def _fmt(template, values):
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return template


def reason(f, sid, ctx):
    """One finding in plain words, from review_types.yaml `reasons` (module
    docstring of review.py); the raw message when the rule has no entry."""
    msg = f["message"]
    r = ctx.reasons.get(f["rule"])
    if r is None:
        return msg
    if r.get("hide"):
        return ""
    other = next((s for s in f.get("subjects") or [] if s != sid), None)
    values = {"message": msg, "other": ctx.name(other) if other else "?"}
    pattern = r.get("pattern")
    if pattern:
        m = re.search(pattern, msg, re.S)
        if not m:
            return _fmt(r["else"], values) if "else" in r else msg
        values.update({k: int(v) if isinstance(v, str) and v.isdigit() else v
                       for k, v in m.groupdict().items()})
    text = r.get("text")
    return _fmt(text, values) if text is not None else msg


def reasons_for(found, sid, ctx):
    rules = {f["rule"] for f in found}
    out = []
    for f in found:
        hide_if = (ctx.reasons.get(f["rule"]) or {}).get("hide_if") or []
        if any(r in rules for r in hide_if):
            continue
        text = reason(f, sid, ctx)
        if text and text not in out:
            out.append(text)
    return out


# ---------------------------------------------------------------- builders
#
# Each builder returns (choices, actions, editor). Messages of the lint rules
# `unreviewed-mapping` and `mapping-target-is-doc-link` are parsed here (they
# are cwb's own lint text; see lint.py).

UNREVIEWED_RE = re.compile(r"' (\S+) (\S+) has no mapping")
DOC_LINK_RE = re.compile(r"(\w+_mappings) value (\S+) is a documentation page")


def _first(found, *rules):
    for rule in rules:
        for f in found:
            if f["rule"] == rule:
                return f
    return None


def _id(f):
    return f["id"] if f else None


def signoff(sid, found, payload, ctx):
    return [], [action("Looks right", "keep", "Reviewed; no problems found.", tone="primary")], None


def links(sid, found, payload, ctx):
    cfg = ctx.answers.get("links") or {}
    predicates = cfg.get("predicates") or {}
    grades = cfg.get("grades") or {}
    choices, actions = [], []
    for f in found:
        if f["rule"] != "mapping-suggestion":
            continue
        s = ctx.suggestions.get(f["id"])
        if not s:
            continue
        source, label = str(s.get("source") or ""), s.get("label") or ""
        obj = to_curie(s["object"], ctx.prefixes)
        op = {"predicate": predicates.get(source, predicates.get("*", "skos:relatedMatch")),
              "object": obj, "object_label": label}
        grade = grades.get(source)
        if grade:
            template = grade.get("rationale") or DEFAULT_GRADE_RATIONALE
            accept = [action(o["label"], "adopt_mapping",
                             _fmt(template, {"label": label, "phrase": o.get("phrase", "")}),
                             operation={**op, grade["field"]: o["value"]}, evidence=f["id"],
                             tone="primary" if i == 0 else "neutral")
                      for i, o in enumerate(grade.get("options") or [])]
        else:
            accept = [action("Accept", "adopt_mapping", f"Accepted suggested link to {obj} ({label}).",
                             operation=op, evidence=f["id"], tone="primary")]
        detail = f"{source.upper()} · similarity {float(s.get('score') or 0):.2f}"
        if s.get("definition"):
            detail += f" · {s['definition']}"
        choices.append({"text": f"{label} ({obj})", "detail": detail, "actions": [
            *accept,
            action("Reject", "reject_mapping", f"{label} is not a match.", operation=op,
                   evidence=f["id"], tone="danger")]})
    unrev = _first(found, "unreviewed-mapping")
    m = unrev and UNREVIEWED_RE.search(unrev["message"])
    if m:
        op = {"predicate": m[1], "object": m[2]}
        choices.append({"text": f"Existing link: {m[1]} {m[2]}", "detail": None, "actions": [
            action("Confirm link", "adopt_mapping", f"Checked the existing link to {m[2]}; it is right.",
                   operation=op, evidence=unrev["id"], tone="primary"),
            action("Remove link", "reject_mapping", f"The existing link to {m[2]} is wrong.",
                   operation=op, evidence=unrev["id"], tone="danger")]})
    doc = _first(found, "mapping-target-is-doc-link")
    m = doc and DOC_LINK_RE.search(doc["message"])
    if m:
        choices.append({"text": f"Link to a web page, not a term: {m[2]}", "detail": None, "actions": [
            action("Remove link", "reject_mapping", "The link points at a documentation page, not a term.",
                   operation={"predicate": m[1], "object": m[2]}, evidence=doc["id"], tone="primary")]})
    actions.append(action(
        "No match exists", "keep",
        ctx.text("No suitable term in outside ontologies; this stays {project}-only."),
        evidence=_id(_first(found, "no-mapping-at-all") or _first(found, "unmapped-high-usage")
                     or _first(found, "coverage-gap") or _first(found, "class-no-alignment"))))
    return choices, actions, None


def definitions(sid, found, payload, ctx):
    f = _first(found, "terse-description") or _first(found, "enum-value-no-description") \
        or _first(found, "todo-marker")
    desc = payload.get("description")
    editor = {"field": "description", "value": desc if desc is not None else "",
              "placeholder": "Write the definition"}
    return [], [
        action("Save new definition", "edit_description", "Rewrote the definition.",
               input="description", evidence=_id(f), tone="primary"),
        action("Definition is fine", "keep", "Definition is clear as it is.", evidence=_id(f)),
    ], editor


def duplicates(sid, found, payload, ctx):
    choices, actions = [], []
    me = ctx.name(sid)
    for f in found:
        if f["rule"] != "similar-terms":
            continue
        other = next((s for s in f.get("subjects") or [] if s != sid), None)
        if not other or other not in ctx.subjects or ctx.subjects[other].get("retired"):
            continue
        them = ctx.name(other)
        choices.append({"text": f'"{me}" vs "{them}"', "detail": None, "actions": [
            action(f'Merge into "{them}"', "merge", f'Same meaning as "{them}".', evidence=f["id"],
                   also=[other], tone="danger", operation={"into": other, "mappings": "move"},
                   confirm=f'Merge "{me}" into "{them}"? This term goes away when applied.'),
            action("Different things", "keep", f'Different from "{them}".', evidence=f["id"],
                   also=[other]),
        ]})
    compound = _first(found, "residual-compound")
    if compound:
        actions.append(action("Keep as one term", "keep_atomic",
                              "This is one concept, not a compound of facets.", evidence=compound["id"]))
    fine = _first(found, "inline-enum") or _first(found, "shared-slot")
    if fine:
        actions.append(action("Fine as is", "keep", "Reviewed; leave as is.", evidence=fine["id"]))
    return choices, actions, None


def keep_or_cut(sid, found, payload, ctx):
    f = _first(found, "zero-usage-term") or _first(found, "orphan-facet-value")
    return [], [
        action("Keep", "keep", "Worth keeping even though nothing uses it yet.", evidence=_id(f),
               tone="primary"),
        action("Delete", "delete", "Unused and not needed.", evidence=_id(f), tone="danger",
               confirm=f'Delete "{ctx.name(sid)}"? It goes away when applied.'),
    ], None


def fields(sid, found, payload, ctx):
    choices, actions, editor = [], [], None
    req = _first(found, "required-policy")
    if req:
        choices.append({"text": "Should this field be required?", "detail": None, "actions": [
            action("Required", "set_cardinality", "Records are meaningless without it; keep it required.",
                   operation={"required": True}, evidence=req["id"]),
            action("Recommended", "set_cardinality", "Useful but not essential; make it recommended.",
                   operation={"required": False, "recommended": True}, evidence=req["id"], tone="primary"),
            action("Optional", "set_cardinality", "Make it optional.",
                   operation={"required": False, "recommended": False}, evidence=req["id"]),
        ]})
    ex = _first(found, "slot-no-examples")
    if ex:
        editor = {"field": "examples", "value": "", "placeholder": "One example value per line"}
        actions.append(action("Save examples", "set_examples", "Added examples.", input="examples",
                              evidence=ex["id"], tone="primary"))
    actions.append(action("Fine as is", "keep", "Field is set up right as it is.",
                          evidence=_id(req or ex)))
    return choices, actions, editor


BUILDERS = {"signoff": signoff, "links": links, "definitions": definitions,
            "duplicates": duplicates, "structure": duplicates, "keep-or-cut": keep_or_cut,
            "fields": fields}


# ---------------------------------------------------------------- items

def item(sid, found, type_def, bundle, status, ctx):
    s = ctx.subjects[sid]
    payload = s.get("payload") or {}
    kind_of_answers = type_def.get("answers_kind") or type_def["id"]
    builder = BUILDERS.get(kind_of_answers)
    choices, actions, editor = builder(sid, found, payload, ctx) if builder else ([], [], None)
    allowed = {v for v, e in (ctx.verdicts.get(s["kind"]) or {}).items() if not (e or {}).get("seed_only")}
    choices = [{**c, "actions": [a for a in c["actions"] if a["verdict"] in allowed]} for c in choices]
    choices = [c for c in choices if c["actions"]]
    actions = [a for a in actions if a["verdict"] in allowed]
    container = s.get("container")
    container_label = ctx.name(container) if container else None
    desc = payload.get("description")
    return {
        "sid": sid, "kind": s["kind"], "slug": s.get("slug"), "title": ctx.name(sid),
        "container": container, "container_label": container_label,
        "browse": ctx.browse.get(sid),
        "description": desc if desc is not None else "",
        "reasons": reasons_for(found, sid, ctx),
        "findings": [f["id"] for f in found],
        "choices": choices, "actions": actions, "editor": editor,
        "group": (container_label or f"Other {s['kind']}s") if type_def.get("fallback") else None,
        "hash": s["hash"], "score": status["subjects"][sid]["score"],
    }


def build_items(bundle, status, track_id, type_id, sorted_=None):
    """The rows of one review type's page, in queue order. `sorted_` is
    `sort(bundle, status)` when the caller already has it."""
    sorted_ = sorted_ or sort(bundle, status)
    track = next(t for t in bundle["review_types"]["tracks"] if t["id"] == track_id)
    type_def = next(ty for ty in track["types"] if ty["id"] == type_id)
    ctx = Ctx(bundle)
    return [item(sid, found, type_def, bundle, status, ctx)
            for sid, found in sorted_[track_id]["types"][type_id]]
