"""ONGA's project plugin for curation-workbench.

Everything here is specific to ONGA: legacy reference forms, the embeddings
findings, ENCODE usage, upstream SO requests, facets and SO element types.
"""
import json
from urllib.parse import quote

import yaml

BASE = "https://databio.org/onga/"


def legacy_forms(tid, enum, label):
    """Old reference forms of a label: the `onga:<underscored label>` CURIE, its
    IRI, and the old gen-owl IRI `<base><Enum>#<percent-encoded label>`."""
    under = label.replace(" ", "_")
    forms = [f"onga:{under}", f"{BASE}{under}"]
    if enum:
        forms.append(f"{BASE}{enum}#{quote(label)}")
    return forms


HAS_ELEMENT_TYPE = "onga:has_element_type"
TIER_SCHEMA = "src/curation_tier.yaml"


def element_type_fit_check(verdict, op):
    """An SO element-type link must say how well it fits (mappings/policy.yaml `fits`)."""
    if verdict == "adopt_mapping" and op.get("predicate") == HAS_ELEMENT_TYPE \
            and "element_type_fit" not in op:
        return [f"operation.element_type_fit is required for {HAS_ELEMENT_TYPE}"]
    return []


def tiers_defined(wb):
    """True once the tiers plan has landed `CurationTier` in src/."""
    from curation_workbench import yamlio
    path = wb.cfg.root / TIER_SCHEMA
    if not path.exists():
        return False
    return "CurationTier" in (yamlio.load(path).data.get("enums") or {})


def promote_tier_check(wb, rec):
    if rec.get("verdict") == "promote_tier" and not tiers_defined(wb):
        return [f"promote_tier needs {TIER_SCHEMA} to define CurationTier"]
    return []


# ---------------------------------------------------------------- findings

REPORTS = "embeddings/outputs/reports"
EMBEDDING_REPORTS = ("internal_similarity.json", "gap_analysis.json", "mapping_report.json")


def embedding_findings(name, data):
    """Normalized findings out of one embeddings report."""
    out = []
    if name == "internal_similarity.json":
        for p in data.get("pairs") or []:
            out.append({"id": p["id"], "source": "embeddings", "rule": "similar-terms",
                        "subjects": p["subjects"],
                        "message": f"{p['term1']} ~ {p['term2']} "
                                   f"(similarity {p['similarity']}; {p.get('machine_recommendation')})"})
    elif name == "gap_analysis.json":
        for group in (data.get("by_subset") or {}).values():
            for t in group.get("terms") or []:
                out.append({"id": t["id"], "source": "embeddings", "rule": "coverage-gap",
                            "subjects": t["subjects"],
                            "message": f"{t['onga_term']}: no ontology match "
                                       f"(best similarity {t['max_similarity']})"})
    elif name == "mapping_report.json":
        for t in data.get("terms") or []:
            for m in t.get("suggested_mappings") or []:
                out.append({"id": m["id"], "source": "embeddings", "rule": "mapping-suggestion",
                            "subjects": m["subjects"], "object": m["term_id"],
                            "message": f"{t['onga_term']} -> {m['term_id']} {m['term_name']} "
                                       f"({m['match_type']}, {m['similarity']})"})
    return out


def embeddings_source(wb, fingerprint, severity_of):
    findings, sources = [], []
    for name in EMBEDDING_REPORTS:
        path = wb.cfg.root / REPORTS / name
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        prov = data.get("provenance")
        if not prov:
            continue  # pre-provenance reports are treated as absent
        stale = prov.get("schema_fingerprint") != fingerprint
        items = [] if stale else embedding_findings(name, data)
        for f in items:
            f["severity"] = severity_of[f["rule"]]
        sources.append({"path": wb.cfg.rel(path), "findings": len(items), "stale": stale})
        findings += items
    return findings, sources


# ---------------------------------------------------------------- signals

def encode_usage(wb):
    """{term sid: ENCODE file count} from curation/usage.json."""
    path = wb.cfg.root / "curation" / "usage.json"
    if not path.exists():
        return {}
    usage = json.loads(path.read_text())
    return {sid: (u or {}).get("encode_files", 0) for sid, u in usage.items()
            if isinstance(u, dict)}


UPSTREAM = "proposals/upstream_requests.yaml"


def upstream(ctx):
    """1 for terms named in upstream SO requests (motivating_terms, onga_terms)."""
    path = ctx.wb.cfg.root / UPSTREAM
    data = (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}
    labels, out = ctx.labels, {}

    def walk(node):
        if isinstance(node, dict):
            for key in ("motivating_terms", "onga_terms"):
                for t in node.get(key) or []:
                    label = t.get("term") if isinstance(t, dict) else t
                    if label in labels:
                        out[f"term:{labels[label]}"] = 1
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(data)
    return out


# ---------------------------------------------------------------- lint

FACET_TSV = "mappings/facet_decomposition.tsv"
FACET_NON_AXIS = {"encode_term", "output_type", "feature_type", "base_exists"}


def _tsv_rows(path):
    import csv
    with open(path) as fh:
        return list(csv.DictReader(
            (line for line in fh if not line.startswith("#")), delimiter="\t"))


def facet_rules(linter):
    """orphan-facet-value: a facet value no decomposition row or slot example uses.
    residual-compound: a content term whose label still names a facet, with no
    keep_atomic decision."""
    from curation_workbench.lint import word_in
    pol = linter.pol
    facet_labels = {e: set() for e in pol["facet_enums"]}
    for t in linter.of_kind["term"]:
        if t["enum"] in facet_labels:
            facet_labels[t["enum"]].add(t["label"])
    facet_words = [w.lower() for w in pol["facet_words"]]
    examples = linter.slot_example_values()
    decomposed = set()
    for r in _tsv_rows(linter.wb.cfg.root / FACET_TSV):
        for k, v in r.items():
            if k not in FACET_NON_AXIS and v and v.strip():
                decomposed.add(v.strip())
    keep_atomic = {d["subject"] for d in linter.wb.store.records()
                   if d.get("verdict") == "keep_atomic" and d.get("status") != "withdrawn"}
    for t in linter.of_kind["term"]:
        sid, label = t["sid"], t["label"]
        if t["enum"] in facet_labels and label not in decomposed and label not in examples:
            linter.emit("orphan-facet-value", [sid],
                        f"{t['enum']} value '{label}' appears in no facet "
                        "decomposition row and no slot example")
        if not linter.is_content(t):
            continue
        hits = sorted({v for vals in facet_labels.values() for v in vals if word_in(v, label)}
                      | {w for w in facet_words if word_in(w, label)})
        if hits and sid not in keep_atomic:
            linter.emit("residual-compound", [sid],
                        f"'{label}' contains facet word(s) {', '.join(hits)} "
                        "and has no keep_atomic decision")


# ---------------------------------------------------------------- apply: label references

SCOPE_TSV = "mappings/scope_delegations.tsv"
BUILD_DATA = "site/scripts/build-data.js"
# facet_decomposition.tsv: the base column per content enum, and the enum each
# facet column draws its values from.
BASE_COLUMNS = {"DataType": "output_type", "FeatureType": "feature_type"}
FACET_COLUMNS = {
    "strand": "StrandOrientation",
    "read_multiplicity": "ReadMultiplicity",
    "filter_status": "FilterStatus",
    "normalization": "Normalization",
    "thresholding": "Thresholding",
    "derivation": "Derivation",
    "reference_build_sex": "ReferenceBuildSex",
    "haplotype_resolution": "HaplotypeResolution",
}


class FacetRefs:
    """facet_decomposition.tsv: base column (output_type / feature_type) and facet columns."""
    name = "facets"

    def find(self, ctx, loc):
        out, label, enum = [], loc.label, loc.enum
        base = BASE_COLUMNS.get(enum)
        for r in ctx.read_tsv(FACET_TSV).rows:
            if base and r.get(base) == label:
                out.append(f"{FACET_TSV} base of {r['encode_term']!r}")
            for col, fenum in FACET_COLUMNS.items():
                if fenum == enum and r.get(col) == label:
                    out.append(f"{FACET_TSV} {col} of {r['encode_term']!r}")
        return out

    def relabel(self, ctx, loc, new_label, new_enum):
        old, enum = loc.label, loc.enum
        old_base, new_base = BASE_COLUMNS.get(enum), BASE_COLUMNS.get(new_enum)
        for r in ctx.read_tsv(FACET_TSV).rows:
            if old_base and r.get(old_base) == old:
                r[old_base] = ""
                r[new_base] = new_label
            for col, fenum in FACET_COLUMNS.items():
                if fenum == enum and r.get(col) == old:
                    r[col] = new_label


class ScopeRefs:
    """scope_delegations.tsv: content_base (+ content_enum)."""
    name = "scope"

    def find(self, ctx, loc):
        return [f"{SCOPE_TSV} content_base of {r['encode_term']!r}"
                for r in ctx.read_tsv(SCOPE_TSV).rows
                if r.get("content_base") == loc.label and r.get("content_enum") in ("", loc.enum)]

    def relabel(self, ctx, loc, new_label, new_enum):
        for r in ctx.read_tsv(SCOPE_TSV).rows:
            if r.get("content_base") == loc.label and r.get("content_enum") in ("", loc.enum):
                r["content_base"] = new_label
                if r.get("content_enum"):
                    r["content_enum"] = new_enum

    def enum_renamed(self, ctx, old, new):
        for r in ctx.read_tsv(SCOPE_TSV).rows:
            if r.get("content_enum") == old:
                r["content_enum"] = new


def upstream_entries(ctx):
    """Every {term, category, ...} entry in upstream_requests.yaml."""
    out = []

    def walk(node):
        if isinstance(node, dict):
            for key in ("motivating_terms", "onga_terms"):
                for e in node.get(key) or []:
                    if isinstance(e, dict) and "term" in e:
                        out.append(e)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(ctx.read_yaml(UPSTREAM).data)
    return out


def _upstream_match(e, label, enum):
    cat = e.get("enum") or e.get("category")
    return e["term"] == label and (not cat or cat == enum)


class UpstreamRefs:
    """proposals/upstream_requests.yaml: motivating_terms[].term, onga_terms[].term."""
    name = "upstream"

    def find(self, ctx, loc):
        return [f"{UPSTREAM} term {loc.label!r}" for e in upstream_entries(ctx)
                if _upstream_match(e, loc.label, loc.enum)]

    def relabel(self, ctx, loc, new_label, new_enum):
        for e in upstream_entries(ctx):
            if _upstream_match(e, loc.label, loc.enum):
                e["term"] = new_label
                for key in ("category", "enum"):
                    if e.get(key) == loc.enum:
                        e[key] = new_enum

    def enum_renamed(self, ctx, old, new):
        for e in upstream_entries(ctx):
            for key in ("category", "enum"):
                if e.get(key) == old:
                    e[key] = new


def install_ref_providers(wb):
    """Order matters (it is the order of refusal messages): see_also, facets,
    scope, sssom, upstream, examples."""
    names = [p.name for p in wb.ref_providers]
    wb.ref_providers.insert(names.index("see_also") + 1, ScopeRefs())
    wb.ref_providers.insert(names.index("see_also") + 1, FacetRefs())
    names = [p.name for p in wb.ref_providers]
    wb.ref_providers.insert(names.index("examples"), UpstreamRefs())


# ---------------------------------------------------------------- apply: checks and hooks

def move_between_content(handler, rec, ctx):
    """A term may not move between a content and a non-content enum while the
    facet or scope tables reference it."""
    from curation_workbench.handlers.content import references, subject
    loc = handler.term(ctx, rec)
    target = subject(ctx, handler.op(rec)["enum"])["name"]
    if (loc.enum in BASE_COLUMNS) != (target in BASE_COLUMNS):
        used = [r for r in references(ctx, loc) if FACET_TSV in r or SCOPE_TSV in r]
        if used:
            return [f"cannot move between a content and a non-content enum while "
                    f"referenced: {used}"]
    return []


def load_mapping_policy(root):
    p = yaml.safe_load(open(root / "mappings" / "policy.yaml"))
    return {"banned_skos": set(p["banned_skos"]), "set_denoting_so": set(p["set_denoting_so"]),
            "sequence_attribute_so": set(p["sequence_attribute_so"]), "fits": set(p["fits"]),
            "subject_category": p["subject_category"], "object_category": p["object_category"]}


def so_mapping_checks(op, negate, ctx):
    """The set/element rule: SO objects take onga:has_element_type with a fit grade;
    banned SKOS predicates may target only set-denoting SO classes and attributes."""
    pred, obj = op["predicate"], op["object"]
    policy = load_mapping_policy(ctx.root)
    so = obj.startswith("SO:")
    errs = []
    if pred == HAS_ELEMENT_TYPE:
        if not so:
            errs.append(f"{HAS_ELEMENT_TYPE} needs an SO object")
        if not negate and op.get("element_type_fit") not in policy["fits"]:
            errs.append(f"element_type_fit must be one of {sorted(policy['fits'])}")
    elif pred in policy["banned_skos"] and so and not negate and obj not in (
            policy["set_denoting_so"] | policy["sequence_attribute_so"]):
        errs.append(f"{pred} against SO is banned (set/element rule); "
                    f"use {HAS_ELEMENT_TYPE} or skos:relatedMatch")
    if op.get("element_type_fit") and pred != HAS_ELEMENT_TYPE:
        errs.append(f"element_type_fit only applies to {HAS_ELEMENT_TYPE}")
    return errs


def live_so_terms(root):
    """{id: name} for every non-obsolete SO term in so.obo (stdlib stanza scan)."""
    path = root / "embeddings" / "data" / "ontologies" / "so.obo"
    if not path.exists():
        return None
    out, cur, name, obsolete, in_term = {}, None, "", False, False

    def flush():
        if in_term and cur and not obsolete:
            out[cur] = name

    with open(path) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("["):
                flush()
                in_term = line == "[Term]"
                cur, name, obsolete = None, "", False
            elif line.startswith("id: "):
                cur = line[4:].strip()
            elif line.startswith("name: "):
                name = line[6:].strip()
            elif line.startswith("is_obsolete: true"):
                obsolete = True
    flush()
    return out


def so_mapping_fill(row, op, negate, table, ctx, new):
    pred, obj = op["predicate"], op["object"]
    if new and pred == HAS_ELEMENT_TYPE and "subject_category" in table.header:
        policy = load_mapping_policy(ctx.root)
        row["subject_category"] = policy["subject_category"]
        row["object_category"] = policy["object_category"]
    if not op.get("object_label") and not row.get("object_label") and obj.startswith("SO:"):
        row["object_label"] = (live_so_terms(ctx.root) or {}).get(obj, "")
    if pred == HAS_ELEMENT_TYPE and "element_type_fit" in table.header:
        row["element_type_fit"] = "" if negate else op["element_type_fit"]


def protected_names(ctx, section):
    """Names ONGA's site hard-codes in build-data.js (and, for classes, the
    Makefile's example checks)."""
    import re
    js = (ctx.root / BUILD_DATA).read_text()
    if section == "enums":
        names = set(re.findall(r"enums\??\.(\w+)", js))
        m = re.search(r"const VOCAB_ENUM_HREFS = \{(.*?)\};", js, re.S)
        if m:
            names |= set(re.findall(r"^\s*(\w+):", m.group(1), re.M))
        return {n: f"named in {BUILD_DATA}" for n in names}
    if section == "classes":
        names = set(re.findall(r"classes\??\.(\w+)", js))
        m = re.search(r"const CLASS_HREF_OVERRIDES = \{(.*?)\};", js, re.S)
        if m:
            names |= set(re.findall(r"^\s*(\w+):", m.group(1), re.M))
        names |= set(re.findall(r"-C (\w+)", (ctx.root / "Makefile").read_text()))
        return {n: f"named in {BUILD_DATA} or the Makefile" for n in names}
    return {}


# ---------------------------------------------------------------- apply: ONGA-only verdicts

def onga_handlers():
    from curation_workbench.handlers import ApplyError, Effects
    from curation_workbench.handlers.content import (Handler, _tid, find_term, references,
                                                     retire, tid_of)

    class TermPromoteTier(Handler):
        kind, verdict = "term", "promote_tier"

        def run(self, rec, ctx):
            self.term(ctx, rec).pv["status"] = "ongatier:approved"

    class TermFacet(Handler):
        """Retire a compound term into a base term plus facet values: a new
        facet_decomposition.tsv row, and the ledger records it as faceted."""
        kind, verdict = "term", "facet"

        def check(self, rec, ctx):
            loc = self.term(ctx, rec)
            op = self.op(rec)
            base = self.term(ctx, rec, op["base"])
            errs = []
            if loc.enum not in BASE_COLUMNS or base.enum not in BASE_COLUMNS:
                errs.append("facet applies to DataType / FeatureType terms and bases only")
            if _tid(base) == _tid(loc):
                errs.append("a term cannot be its own base")
            if not op["facets"]:
                errs.append("operation.facets names no facet")
            for col, sid in op["facets"].items():
                if col not in FACET_COLUMNS:
                    errs.append(f"unknown facet column {col!r}; one of {sorted(FACET_COLUMNS)}")
                    continue
                s = ctx.subjects.get(sid) if isinstance(sid, str) else None
                if s is None or s["kind"] != "term" or s["enum"] != FACET_COLUMNS[col]:
                    errs.append(f"facets.{col} must be a {FACET_COLUMNS[col]} term id, got {sid!r}")
            if any(r["encode_term"] == loc.label for r in ctx.read_tsv(FACET_TSV).rows):
                errs.append(f"{FACET_TSV} already decomposes {loc.label!r}")
            refs = [r for r in references(ctx, loc) if not r.startswith(f"{FACET_TSV} base of")]
            if refs:
                errs.append(f"{rec['subject']} is still referenced: " + "; ".join(refs))
            return errs

        def run(self, rec, ctx):
            loc = self.term(ctx, rec)
            op = self.op(rec)
            base = self.term(ctx, rec, op["base"])
            table = ctx.read_tsv(FACET_TSV)
            row = {c: "" for c in table.header}
            row["encode_term"] = loc.label
            row[BASE_COLUMNS[base.enum]] = base.label
            for col, sid in op["facets"].items():
                row[col] = find_term(ctx, tid_of(ctx, sid)).label
            row["base_exists"] = "yes"
            table.rows.append(row)
            del loc.pvs[loc.label]
            retire(ctx, _tid(loc), "faceted", loc.enum, loc.label, rec["id"], replaced_by=_tid(base))
            return Effects(retired=[rec["subject"]])

    _ = ApplyError
    return [TermPromoteTier(), TermFacet()]


# ---------------------------------------------------------------- daemon

REQUEST_STATUSES = ("proposed", "filed", "accepted", "declined", "withdrawn")


def patch_proposal(api, req_id):
    """PATCH /api/proposals/<id>: set `status:` of an upstream SO request."""
    from curation_workbench import store, yamlio
    body = api.json_body()
    status = body.get("status")
    if status not in REQUEST_STATUSES:
        raise store.StoreError(400, [f"status must be one of {list(REQUEST_STATUSES)}"])
    path = api.workbench.cfg.root / UPSTREAM
    doc = yamlio.load(path)
    for reqs in (doc.data.get("requests") or {}).values():
        for req in reqs:
            if req.get("id") == req_id:
                req["status"] = status
                yamlio.dump(doc, path)
                return api.send(200, {"id": req_id, "status": status})
    raise store.StoreError(404, [f"no upstream request {req_id}"])


def register(wb):
    wb.routes.append(("PATCH", "/api/proposals/", patch_proposal))
    wb.legacy_forms.append(legacy_forms)
    wb.operation_checks.append(element_type_fit_check)
    wb.record_checks.append(promote_tier_check)
    wb.findings_sources.append(embeddings_source)
    wb.term_usage.append(encode_usage)
    wb.signals["upstream"] = upstream
    wb.lint_rules.append(facet_rules)
    install_ref_providers(wb)
    wb.handler_check("term", "move_to_enum", move_between_content)
    wb.mapping_checks.append(so_mapping_checks)
    wb.mapping_fill.append(so_mapping_fill)
    wb.protected_names.append(protected_names)
    for h in onga_handlers():
        wb.handler(h)
