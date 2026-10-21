"""The review UI's read bodies (`views`) on the toy project, called directly
(the hosted API returns exactly these). Runs on a temp copy of the toy."""
import json
import shutil
from pathlib import Path

import pytest

from curation_workbench import derive, review, snapshot, store, views
from curation_workbench.store import landing
from curation_workbench.workbench import Workbench

TOY = Path(__file__).parent / "fixtures" / "toy"


@pytest.fixture
def wb(tmp_path):
    root = tmp_path / "toy"
    shutil.copytree(TOY, root)
    return Workbench.open(root / "workbench.yaml")


def roundtrip(x):
    return json.loads(json.dumps(x))


def test_views(wb):
    bundle = snapshot.build(wb)
    h = bundle["subjects"]["subjects"]["term:TOY_0001"]["hash"]
    landing.append_hosted(wb.cfg.decisions, wb.cfg.decisions, [store.normalize(
        {"id": "H-0001", "subject": "term:TOY_0001", "verdict": "keep", "rationale": "fine",
         "decided_by": "tester", "decided_on": "2026-10-21", "subject_hashes": {"term:TOY_0001": h},
         "schema_fingerprint": bundle["schema_fingerprint"], "status": "pending",
         "origin": {"kind": "hosted", "ref": "H-0001"}})])
    records = wb.store.records()
    st = derive.status(bundle, records)
    rv = derive.review(bundle, st, review.next_id(wb.store))
    idx = views.review_index(rv)
    assert all("items" not in ty and "sids" in ty for t in idx["tracks"] for ty in t["types"])
    page = views.review_type_page(rv, "schema", "fields")
    assert set(page) == {"track", "type", "title", "decisions_next_id", "items"}
    assert page["decisions_next_id"] == "DEC-0002"
    assert views.review_type_page(rv, "schema", "nope") is None
    sub = roundtrip(views.subject_view(bundle, st, records, "term:TOY_0001"))
    assert set(sub) == {"subject", "status", "findings", "decisions", "aliases", "prompts", "panels", "browse"}
    assert [d["id"] for d in sub["decisions"]] == ["DEC-0001"]
    assert views.subject_view(bundle, st, records, "term:NOPE") is None
    assert set(views.verdicts_view(bundle["verdicts"])) >= {"term"}
    found = roundtrip(views.findings_view(bundle, st))
    # Every finding carries `text`: the plain words of the project's `reasons`,
    # or the raw message when (as in the toy) it has none.
    assert found["findings"] and all(f["text"] == f["message"] for f in found["findings"])
    assert all(f["text"] == f["message"] for f in sub["findings"])
    assert views.history(records, bundle)["history"][0]["id"] == "DEC-0001"
    pend = views.pending_view(records)
    assert [d["id"] for d in pend["decisions"]] == ["DEC-0001"] and pend["ok"] and pend["errors"] == {}
