"""Stage 2: the decision store accepts and rejects exactly what ONGA's does,
and writes byte-identical files."""
import copy
import datetime
import shutil

import pytest
from conftest import ADAPTER, legacy

from curation_workbench import store
from curation_workbench.workbench import Workbench


@pytest.fixture(scope="module")
def env(onga):
    old = legacy(onga, "workbench.store")
    wb = Workbench.open(ADAPTER, root=onga)
    subj_old = old.Subjects.load(onga / "curation" / "subjects.json")
    subj_new = store.Subjects.load(onga / "curation" / "subjects.json")
    verd_old = old.load_verdicts(onga / "curation" / "verdicts.yaml")
    verd_new = store.load_verdicts(wb.cfg.verdicts)
    return old, wb, (subj_old, verd_old), (subj_new, verd_new)


def outcome(fn):
    try:
        fn()
        return ("ok",)
    except Exception as e:  # StoreError from either module
        return (getattr(e, "status", type(e).__name__), tuple(getattr(e, "errors", [str(e)])))


def base(subjects, sid, verdict="keep", **kw):
    s = subjects.get(sid)
    rec = {"id": "DEC-9999", "subject": sid, "verdict": verdict, "operation": None,
           "rationale": "because", "also_affects": [], "evidence": [],
           "decided_by": "tester", "decided_on": datetime.date(2026, 10, 21),
           "subject_hashes": {sid: s["hash"] if s else "x"}, "schema_fingerprint": subjects.fingerprint,
           "status": "pending", "supersedes": None, "origin": {"kind": "local", "ref": None},
           "applied": {"on": None, "subject_hashes_after": None, "created": [], "retired": []}}
    rec.update(kw)
    return rec


def cases(subjects):
    term = next(s for s, r in subjects.by_sid.items() if r["kind"] == "term" and not r["retired"])
    term2 = [s for s, r in subjects.by_sid.items() if r["kind"] == "term" and not r["retired"]][1]
    slot = next(s for s, r in subjects.by_sid.items() if r["kind"] == "slot")
    cls = next(s for s, r in subjects.by_sid.items() if r["kind"] == "class")
    retired = next((s for s, r in subjects.by_sid.items() if r["retired"]), None)
    so = {"op": "adopt_mapping", "predicate": "onga:has_element_type", "object": "SO:0000001"}
    out = [
        base(subjects, term),
        base(subjects, term, rationale=""),
        base(subjects, term, verdict="nonsense"),
        base(subjects, "term:NOPE"),
        base(subjects, term, verdict="adopt_mapping", operation=so),
        base(subjects, term, verdict="adopt_mapping", operation={**so, "element_type_fit": "exact"}),
        base(subjects, term, verdict="adopt_mapping", operation={**so, "element_type_fit": "bogus"}),
        base(subjects, term, verdict="edit_description", operation={"op": "edit_description"}),
        base(subjects, term, verdict="edit_description", operation={"op": "edit_description", "description": "x", "extra": 1}),
        base(subjects, term, verdict="merge", operation={"op": "merge", "into": term2, "mappings": "move"},
             also_affects=[term2], subject_hashes={term: subjects.get(term)["hash"], term2: subjects.get(term2)["hash"]}),
        base(subjects, term, verdict="merge", operation={"op": "merge", "into": slot, "mappings": "move"}),
        base(subjects, term, verdict="promote_tier", operation={"op": "promote_tier", "tier": "reviewed"}),
        base(subjects, term, also_affects=[term]),
        base(subjects, term, evidence=[{"kind": "weird"}]),
        base(subjects, term, subject_hashes={term: "0" * 64}),
        base(subjects, term, subject_hashes={}),
        base(subjects, term, supersedes="DEC-0001"),
        base(subjects, term, supersedes="DEC-8888"),
        base(subjects, term, origin={"kind": "elsewhere"}),
        base(subjects, term, decided_by=""),
        base(subjects, term, id="DEC-1"),
        base(subjects, slot, verdict="set_cardinality", operation={"op": "set_cardinality", "required": False}),
        base(subjects, slot, verdict="set_cardinality", operation={"op": "set_cardinality"}),
        base(subjects, slot, verdict="set_examples", operation={"op": "set_examples", "examples": "x"}),
        base(subjects, cls, verdict="add_slot", operation={"op": "add_slot"}),
        base(subjects, cls, verdict="defer", rationale=""),
    ]
    if retired:
        out.append(base(subjects, retired, verdict="edit_description",
                        operation={"op": "edit_description", "description": "x"}))
    return out


def test_validate_matches(env, onga):
    old, wb, (so, vo), (sn, vn) = env
    doc_old = old.load(onga / "curation" / "decisions.yaml")
    doc_new = store.load(wb.cfg.decisions)
    recs = cases(sn)
    # Every stored decision, re-validated without the hash check, too.
    recs += [r for r in store.plain_records(wb.cfg.decisions)]
    for rec in recs:
        r = copy.deepcopy(rec)
        if isinstance(r.get("decided_on"), str):
            r["decided_on"] = datetime.date.fromisoformat(r["decided_on"])
        a = outcome(lambda: old.validate(copy.deepcopy(r), so, vo, doc=doc_old,
                                         check_hashes=r["status"] == "pending"))
        b = outcome(lambda: store.validate(copy.deepcopy(r), sn, vn, doc=doc_new,
                                           check_hashes=r["status"] == "pending", wb=wb))
        assert a == b, (r["id"], r["subject"], r["verdict"], a, b)


def test_writes_match(env, onga, tmp_path):
    old, wb, (so, vo), (sn, vn) = env
    pa, pb = tmp_path / "a.yaml", tmp_path / "b.yaml"
    shutil.copy(onga / "curation" / "decisions.yaml", pa)
    shutil.copy(onga / "curation" / "decisions.yaml", pb)
    term = next(s for s, r in sn.by_sid.items() if r["kind"] == "term" and not r["retired"])
    draft = {"subject": term, "verdict": "keep", "rationale": "fine", "decided_by": "tester",
             "decided_on": "2026-10-21", "subject_hashes": {term: sn.get(term)["hash"]},
             "evidence": [{"kind": "finding", "id": "lint:x"}]}
    a = old.create(copy.deepcopy(draft), so, vo, path=pa)
    b = store.create(copy.deepcopy(draft), sn, vn, pb, wb=wb)
    assert a == b
    assert pa.read_text() == pb.read_text()
    edit = {**a, "rationale": "changed"}
    old.update(a["id"], copy.deepcopy(edit), so, vo, path=pa)
    store.update(b["id"], copy.deepcopy(edit), sn, vn, pb, wb=wb)
    assert pa.read_text() == pb.read_text()
    old.withdraw(a["id"], path=pa)
    store.withdraw(b["id"], pb)
    assert pa.read_text() == pb.read_text()
