"""Stage 5: `cwb apply --dry-run` stages the same edits as ONGA's
scripts/apply_decisions.py --dry-run, for a battery of decisions covering
every handler family. Runs in ONGA_SCRATCH (a disposable worktree): the test
appends pending decisions to its curation/decisions.yaml and restores the file
afterwards. Nothing is written by either engine (dry runs)."""
import json

import pytest
from conftest import ADAPTER, legacy

from curation_workbench import apply, store
from curation_workbench.workbench import Workbench


def pick(subjects, kind, **match):
    for sid, s in subjects.items():
        if s["kind"] == kind and not s.get("retired") and all(s.get(k) == v for k, v in match.items()):
            return sid
    raise LookupError(f"no {kind} {match}")


def battery(S):
    """[(subject, verdict, operation, also_affects)] covering every handler family."""
    terms = [sid for sid, s in S.items() if s["kind"] == "term" and not s.get("retired")]
    by_enum = {}
    for sid in terms:
        by_enum.setdefault(S[sid]["enum"], []).append(sid)
    dt = by_enum["DataType"]
    ft = by_enum["FeatureType"]
    filt = by_enum.get("FilterStatus", [])
    so_obj = "SO:0000001"
    out = [
        (dt[3], "edit_description", {"description": "A rewritten definition for testing."}),
        (dt[4], "rename", {"label": "renamed test term"}),
        (ft[0], "adopt_mapping", {"predicate": "onga:has_element_type", "object": so_obj,
                                  "element_type_fit": "approximate"}),
        (ft[1], "adopt_mapping", {"predicate": "skos:closeMatch", "object": so_obj}),
        (dt[5], "reject_mapping", {"predicate": "skos:closeMatch", "object": "edam:data_0006"}),
        (dt[6], "add_see_also", {"target": dt[7]}),
        (dt[8], "set_subsets", {"subsets": [pick(S, "subset", name="alignment")]}),
        (dt[9], "merge", {"into": dt[10], "mappings": "move"}),
        (dt[11], "delete", None),
        (ft[2], "move_to_enum", {"enum": "enum:DataType"}),
        (ft[3], "promote_tier", None),
        (dt[12], "facet", {"base": dt[0], "facets": {"filter_status": filt[0]}} if filt else None),
        ("enum:FeatureType", "add_term", {"label": "brand new feature", "description": "Added in a test."}),
        ("enum:DataType", "edit_description", {"description": "Data types, rewritten."}),
        ("enum:FeatureType", "rename_enum", {"name": "FeatureKind"}),
        (pick(S, "subset", name="alignment"), "rename_subset", {"name": "aligned_reads"}),
        (pick(S, "subset", name="annotation"), "merge_subset", {"into": pick(S, "subset", name="alignment")}),
        ("class:Donor", "edit_description", {"description": "Who the sample came from."}),
        ("class:Donor", "adopt_mapping", {"predicate": "exact_mappings", "object": "NCIT:C16960"}),
        ("class:Donor", "reject_mapping", {"predicate": "close_mappings", "object": "phenopackets_info:individual.html"}),
        ("class:Contact", "rename_class", {"name": "ContactPoint"}),
        ("class:Donor", "rename_class", {"name": "Organism"}),
        ("class:Study", "set_lineage", {"conforms_to": ["dcat:Dataset"]}),
        ("class:Checksum", "add_rule", {"rule": {"preconditions": {"slot_conditions": {"type": {"equals_string": "md5"}}}}}),
        ("slot:elements_circular", "set_cardinality", {"required": False, "recommended": True}),
        ("slot:edge_weight_type", "set_examples", {"examples": [{"value": "count"}]}),
        ("slot:edge_weight_type", "edit_description", {"description": "What the edge weight measures."}),
        ("slot:contact", "rename_slot", {"name": "contact_point"}),
        ("slot:edge_weight_type", "retype_slot", {"range": "string"}),
        ("slot:edge_weight_type", "adopt_mapping", {"predicate": "close_mappings", "object": "EDAM:data_0006"}),
        ("module:contact", "edit_description", {"description": "Contact details."}),
        ("module:contact", "set_layer", {"layer": 4}),
    ]
    return [(sid, v, op) for sid, v, op in out if op is not None or v in ("delete", "promote_tier")]


def same_errors(a, b):
    """Errors match, except where ONGA names its own site files (build-data.js /
    the Makefile) in a protected-name refusal: the generic message says
    'is protected (named in ...)' instead."""
    if sorted(a) != sorted(b):
        return False
    for k in a:
        if len(a[k]) != len(b[k]):
            return False
        for x, y in zip(a[k], b[k]):
            if x != y and not ("build-data.js" in x and "is protected" in y):
                return False
    return True


@pytest.fixture
def decisions(scratch):
    wb = Workbench.open(ADAPTER, root=scratch)
    path = wb.cfg.decisions
    original = path.read_text()
    subjects = apply.fresh_subjects(wb)
    ctx = store.ValidationContext(subjects=subjects, verdicts=store.load_verdicts(wb.cfg.verdicts), wb=wb)
    ids = []
    try:
        for sid, verdict, op in battery(subjects.by_sid):
            also = [v for v in (op or {}).values()
                    if isinstance(v, str) and v in subjects.by_sid and v != sid]
            rec = {"subject": sid, "verdict": verdict, "rationale": f"test {verdict}",
                   "decided_by": "tester", "decided_on": "2026-10-21",
                   "operation": {"op": verdict, **op} if op else None,
                   "also_affects": also,
                   "subject_hashes": {s: subjects.get(s)["hash"] for s in [sid, *also]}}
            try:
                ids.append(wb.store.create(rec, ctx)["id"])
            except store.StoreError:
                pass  # refused at record time (e.g. promote_tier before tiers exist)
        yield wb, ids
    finally:
        path.write_text(original)


def test_dry_run_matches_each(scratch, decisions):
    wb, ids = decisions
    old = legacy(scratch, "workbench.apply")
    for rid in ids:
        a = old.run(want=[rid], dry_run=True)
        b = apply.run(wb, want=[rid], dry_run=True)
        assert a.ok == b.ok, (rid, a.errors, b.errors)
        assert same_errors(a.errors, b.errors), (rid, a.errors, b.errors)
        assert a.diff == b.diff, rid
        assert [r["id"] for r in a.records] == [r["id"] for r in b.records]
        for k in a.effects:
            assert vars(a.effects[k]) == vars(b.effects[k]), rid


def test_dry_run_matches_together(scratch, decisions):
    wb, ids = decisions
    old = legacy(scratch, "workbench.apply")
    ok = [rid for rid in ids if old.run(want=[rid], dry_run=True).ok]
    a = old.run(want=ok, dry_run=True)
    b = apply.run(wb, want=ok, dry_run=True)
    assert same_errors(a.errors, b.errors), (a.errors, b.errors)
    assert [r["id"] for r in a.records] == [r["id"] for r in b.records]
    assert a.diff == b.diff
    print(json.dumps({"applied_together": len(b.records), "refused": b.errors}, indent=1))
