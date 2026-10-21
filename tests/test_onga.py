"""ONGA, the first resident project, through its own workbench.yaml.

ONGA's committed outputs are the golden files: `cwb subjects|lint|status|review
--check` must find them up to date (ONGA's `make test` runs the same checks).
Read-only on ONGA_ROOT; the apply dry run and the review UI bodies use
ONGA_SCRATCH (a disposable ONGA worktree).
"""
import json

import pytest

from curation_workbench import apply, cli, store
from curation_workbench.workbench import Workbench


@pytest.mark.parametrize("cmd", ["subjects", "lint", "status", "review"])
def test_committed_outputs_are_current(onga, cmd, capsys):
    assert cli.main(["-c", str(onga / "workbench.yaml"), cmd, "--check"]) == 0, capsys.readouterr().out


def test_decisions_validate(onga):
    """Every record normalizes; every one not yet applied validates against the
    current registry (applied ones may name subjects their apply retired)."""
    wb = Workbench.open(onga / "workbench.yaml")
    recs = wb.store.records()
    assert recs
    subjects = store.Subjects.load(wb.cfg.output("subjects.json"))
    verdicts = store.load_verdicts(wb.cfg.verdicts)
    known = {r["id"] for r in recs}
    for r in recs:
        rec = store.normalize(r)
        if rec["status"] != "applied":
            store.validate(rec, subjects, verdicts, known_ids=known, check_hashes=False, wb=wb)


def test_plugin_loaded_from_plugin_dir(onga):
    wb = Workbench.open(onga / "workbench.yaml")
    assert wb.cfg.plugin_dir == (onga / "curation" / "plugin").resolve()
    assert wb.plugins and wb.browse_url is not None


def test_apply_dry_run(scratch, tmp_path):
    wb = Workbench.open(scratch / "workbench.yaml")
    emit = tmp_path / "pending.json"
    assert apply.main(wb, dry_run=True, emit_path=emit) == 0
    data = json.loads(emit.read_text())
    pending = [r["id"] for r in wb.store.records() if r["status"] == "pending"]
    assert sorted(d["id"] for d in data["decisions"]) == sorted(pending)


def test_review_ui_bodies(scratch):
    """The review UI's read bodies on ONGA (`views`, as the hosted API returns them)."""
    from curation_workbench import derive, review, snapshot, views
    wb = Workbench.open(scratch / "workbench.yaml")
    bundle = snapshot.build(wb)
    records = wb.store.records()
    st = derive.status(bundle, records)
    rv = derive.review(bundle, st, review.next_id(wb.store))
    idx = views.review_index(rv)
    assert idx["version"] == 2 and idx["more_links"]
    committed = json.loads((scratch / "curation" / "review.json").read_text())
    assert idx["progress"]["waiting"] == sum(t["total"] for t in committed["tracks"])
    links = next(ty for t in committed["tracks"] if t["id"] == "vocabulary"
                 for ty in t["types"] if ty["id"] == "links")
    page = views.review_type_page(rv, "vocabulary", "links")
    assert len(page["items"]) == links["count"] and page["type"]["answers_kind"] == "links"
    assert any(c["text"].endswith(")") for i in page["items"] for c in i["choices"])
    sidx = views.subject_index(bundle, st)
    slot = next(s for s, r in sidx.items() if r["kind"] == "slot" and r["layer"] == 2 and not r["retired"])
    assert any(p["decide_on"] == "owner_class" for p in views.subject_view(bundle, st, records, slot)["prompts"])
    term = views.subject_view(bundle, st, records, "term:ONGA_0000001")
    assert term["browse"].startswith("https://dev.databio.org/onga/")
    assert any(p["title"] == "Usage" for p in term["panels"])
    assert set(views.verdicts_view(bundle["verdicts"])["term"]) >= {"keep", "adopt_mapping"}
    assert views.findings_view(bundle, st)["findings"]
    assert views.history(records, bundle)["history"][0]["id"].startswith("DEC-")
