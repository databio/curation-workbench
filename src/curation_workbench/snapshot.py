"""The commit phase of regen: one JSON bundle per commit of a project's repo.

`build(wb)` computes everything that depends only on the repo at one commit
(the subject registry, aliases, lint and plugin findings, every queue signal)
and returns it as one plain-JSON dict. It is the only place plugin code runs
during regen; `derive.py` turns a bundle plus a decision list into status.json
and review.json with no plugin calls. The project repo commits the bundle
(`cwb regen` writes it to workbench.yaml's `snapshot:` path); the website
downloads that file from GitHub and stores it per commit, so it never runs
project code.

Bundle (format `cwb-snapshot/1`):

  {format, project, schema_fingerprint,
   subjects: <subjects.json doc>, crosswalk: <term_crosswalk.json doc>,
   aliases: <subject_aliases.json doc>, lint: <schema_lint.json doc>,
   findings: [finding ...], sources: [{path, findings, stale}],
   signals: {name: {sid: value}},          # every queue weight except findings/stale
   masked_signals: {name: {sid: value}},   # zeroed by derive where a terminal decision exists
   policy, verdicts, review_types,          # parsed YAML (review_types may be null)
   options: {iri_prefixes},                 # only the options derive needs
   repo_decisions: [record ...],            # the committed decisions.yaml, read-only
   suggestions: {finding id: {object, label, source, score, definition}},
   browse: {sid: url}, panels: {sid: [panel]}, prompts: {sid: [prompt]},
                                            # the review UI's plugin hooks (review_hooks)
   decision_hooks: bool,                    # plugins registered decision checks
   plugins: [module names], warnings: [str]}

Aliases come from the committed decisions.yaml (applied renames), as do any
lint rules that read decisions: both are part of the commit, not of the live
hosted decisions.
"""
import json

from . import lint, review, status, subjects
from .store import load_verdicts

FORMAT = "cwb-snapshot/1"
LINT_FILE = "schema_lint.json"
#: Bundle keys filled by the review UI's plugin hooks (`review_hooks`).
REVIEW_HOOK_KEYS = ("suggestions", "browse", "panels", "prompts")


def plain(obj):
    """A JSON round trip: what a stored bundle looks like when read back."""
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))


def build(wb):
    cfg = wb.cfg
    doc, crosswalk, alias_map, _ = subjects.document(wb)
    doc, crosswalk, alias_map = plain(doc), plain(crosswalk), plain(alias_map)
    fingerprint = doc["schema_fingerprint"]
    lint_doc = plain(lint.build(wb, doc))

    policy = status.load_yaml(cfg.policy, {}) or {}
    severity_of = (policy.get("status") or {}).get("finding_severity") or {}
    findings, sources = status.load_findings(wb, fingerprint, severity_of,
                                             generated={LINT_FILE: lint_doc})

    ctx = status.Context(wb, doc["subjects"], crosswalk.get("labels") or {})
    sig, masked = {}, {}
    for name in policy.get("queue") or {}:
        if name in ("findings", "stale"):
            continue
        if name in wb.signals:
            sig[name] = wb.signals[name](ctx)
        elif name in wb.masked_signals:
            masked[name] = wb.masked_signals[name](ctx)

    hooks = review_hooks(wb, doc["subjects"], crosswalk.get("labels") or {}, {**sig, **masked})
    warnings = list(wb.warnings)
    warnings += [f"findings source {s['path']} is from an older schema (stale); it contributes nothing"
                 for s in sources if s.get("stale")]
    bundle = {
        "format": FORMAT,
        "project": cfg.name,
        "schema_fingerprint": fingerprint,
        "subjects": doc,
        "crosswalk": crosswalk,
        "aliases": alias_map,
        "lint": lint_doc,
        "findings": findings,
        "sources": sources,
        "signals": sig,
        "masked_signals": masked,
        "policy": policy,
        "verdicts": load_verdicts(cfg.verdicts) if cfg.verdicts.exists() else {},
        "review_types": review.load(wb),
        "options": {"iri_prefixes": wb.option("iri_prefixes", {}) or {}},
        "repo_decisions": wb.store.records(),
        **hooks,
        "decision_hooks": bool(wb.operation_checks or wb.record_checks or wb.mapping_checks),
        "plugins": [m.__name__ for m in wb.plugins],
        "warnings": warnings,
    }
    return plain(bundle)


def review_hooks(wb, subjects_, labels, signals):
    """The review UI's plugin hooks: suggestions, browse links, subject panels
    and review prompts, merged across plugins. They see only `wb` and
    `wb.snapshot_view` (no decisions)."""
    out = {k: {} for k in REVIEW_HOOK_KEYS}
    wb.snapshot_view = {"subjects": subjects_, "labels": labels, "signals": signals}
    try:
        for fn in wb.suggestions:
            out["suggestions"].update(fn(wb) or {})
        if wb.browse_url is not None:
            for sid, s in subjects_.items():
                url = wb.browse_url(wb, s)
                if url:
                    out["browse"][sid] = url
        for key, fns in (("panels", wb.subject_panels), ("prompts", wb.review_prompts)):
            for fn in fns:
                for sid, rows in (fn(wb) or {}).items():
                    out[key].setdefault(sid, []).extend(rows)
    finally:
        wb.snapshot_view = None
    return out


def files(wb, bundle):
    """{output path: text} for the registry files and schema_lint.json, exactly
    as `cwb subjects` and `cwb lint` write them."""
    out = subjects.files(wb, bundle["subjects"], bundle["crosswalk"], bundle["aliases"])
    out[wb.cfg.findings_dir / LINT_FILE] = lint.dump(bundle["lint"])
    return out


def dumps(bundle):
    return json.dumps(bundle, ensure_ascii=False, separators=(",", ":")) + "\n"


def dump(bundle):
    """The committed snapshot file: indented like status.json, so diffs read."""
    return json.dumps(bundle, indent=1, ensure_ascii=False) + "\n"


def check_format(bundle):
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
        raise ValueError(f"not a {FORMAT} bundle")
    return bundle
