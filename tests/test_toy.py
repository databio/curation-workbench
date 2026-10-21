"""The generic path on its own: a tiny LinkML project with no plugins goes
through regen, decisions and a real apply. Runs on a temp copy of
tests/fixtures/toy, so nothing in the repo changes."""
import json
import shutil
from pathlib import Path

import pytest

from curation_workbench import apply, lint, review, status, store, subjects
from curation_workbench.workbench import Workbench

TOY = Path(__file__).parent / "fixtures" / "toy"


@pytest.fixture
def toy(tmp_path):
    root = tmp_path / "toy"
    shutil.copytree(TOY, root)
    wb = Workbench.open(root / "workbench.yaml")
    files, counts = subjects.render(wb)
    for p, text in files.items():
        p.write_text(text)
    (wb.cfg.findings_dir).mkdir(parents=True, exist_ok=True)
    text, _ = lint.render(wb)
    (wb.cfg.findings_dir / "schema_lint.json").write_text(text)
    text, _ = status.render(wb)
    wb.cfg.output("status.json").write_text(text)
    return wb


def test_regen(toy):
    reg = json.loads(toy.cfg.output("subjects.json").read_text())
    assert reg["counts"] == {"term": 2, "enum": 1, "class": 1, "slot": 2, "usage": 0,
                             "subset": 0, "module": 2, "total": 8}
    assert reg["subjects"]["module:things"]["layer"] is None
    found = json.loads((toy.cfg.findings_dir / "schema_lint.json").read_text())["counts"]
    assert found["required-policy"] == 1          # color is required, not an identifier
    assert found["unreviewed-mapping"] == 1       # red's ex:RED row
    assert found["enum-value-no-description"] == 1  # blue
    st = json.loads(toy.cfg.output("status.json").read_text())
    assert st["subjects"]["term:TOY_0001"]["signals"]["adoption"] == 1
    assert "slot:color" in st["queue"]
    counts = {t["id"]: {ty["id"]: ty["count"] for ty in t["types"]} for t in review.build(toy)["tracks"]}
    assert counts["schema"]["fields"] == 1   # slot:color, required-policy


def record(wb, sid, verdict, op=None, **kw):
    subj = store.Subjects.load(wb.cfg.output("subjects.json"))
    rec = {"subject": sid, "verdict": verdict, "rationale": "test", "decided_by": "tester",
           "decided_on": "2026-10-21", "operation": {"op": verdict, **op} if op else None,
           "subject_hashes": {sid: subj.get(sid)["hash"]}, **kw}
    ctx = store.ValidationContext(subjects=subj, verdicts=store.load_verdicts(wb.cfg.verdicts), wb=wb)
    return wb.store.create(rec, ctx)


def test_apply(toy):
    record(toy, "term:TOY_0002", "edit_description", {"description": "The color of the sky."})
    record(toy, "slot:color", "set_cardinality", {"required": False, "recommended": True})
    record(toy, "term:TOY_0001", "adopt_mapping", {"predicate": "skos:exactMatch", "object": "ex:RED"})
    record(toy, "enum:Color", "add_term", {"label": "green", "description": "The color of grass."})
    dry = apply.run(toy, dry_run=True)
    assert dry.ok, dry.errors
    assert sorted(dry.diff) == ["curation/term_ids.tsv", "mappings/ex.sssom.tsv", "src/things.yaml"]
    real = apply.run(toy)
    assert real.ok, real.errors
    things = (toy.cfg.schema_dir / "things.yaml").read_text()
    assert "The color of the sky." in things and "recommended: true" in things
    assert "meaning: toy:TOY_0003" in things
    ledger = toy.cfg.ledger.read_text()
    assert ledger.startswith("# toy term ledger") and "TOY_0003\tlive" in ledger
    assert "semapv:ManualMappingCuration" in (toy.cfg.root / "mappings" / "ex.sssom.tsv").read_text()
    recs = toy.store.records()
    assert [r["status"] for r in recs] == ["applied"] * 4
    st = json.loads(toy.cfg.output("status.json").read_text())
    assert st["subjects"]["term:TOY_0003"]["state"] == "applied"
