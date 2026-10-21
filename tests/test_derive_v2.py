"""derive.review: review.json version 2 from the bundle and status equals
`review.build` for the toy, derive runs no plugin code at all, and the toy's
review types (no reasons, no plugins) build items with raw-message reasons
and the generic answers."""
import builtins
import importlib
import json
import shutil
from pathlib import Path

import pytest

from curation_workbench import answers, derive, review, snapshot
from curation_workbench.store import ANSWER_RE
from curation_workbench.workbench import Workbench

TOY = Path(__file__).parent / "fixtures" / "toy"


@pytest.fixture
def toy(tmp_path):
    root = tmp_path / "toy"
    shutil.copytree(TOY, root)
    return root


def bundle_of(root):
    wb = Workbench.open(root / "workbench.yaml")
    return wb, json.loads(snapshot.dumps(snapshot.build(wb)))


def test_review_equals_review_build(toy):
    wb, bundle = bundle_of(toy)
    st = derive.status(bundle, [])
    got = derive.review(bundle, st, 1)
    assert got == review.build(wb)
    assert got["version"] == 2 and got["title"] == "Toy" and got["decisions_next_id"] == "DEC-0001"
    assert got["progress"] == {"waiting": len(st["queue"]),
                               "percent_settled": st["rollups"]["overall"]["percent_settled"]}
    counts = {t["id"]: {ty["id"]: ty["count"] for ty in t["types"]} for t in got["tracks"]}
    assert counts["schema"]["fields"] == 1 and counts["vocabulary"]["links"] == 2


def test_toy_items(toy):
    _, bundle = bundle_of(toy)
    st = derive.status(bundle, [])
    items = {(t["id"], ty["id"]): ty["items"] for t in derive.review(bundle, st, 1)["tracks"]
             for ty in t["types"]}
    red = next(i for i in items["vocabulary", "links"] if i["sid"] == "term:TOY_0001")
    # No `reasons` in the toy's review_types.yaml: the raw lint message.
    assert red["reasons"] == ["'red' skos:exactMatch ex:RED has no mapping justification"]
    assert [c["text"] for c in red["choices"]] == ["Existing link: skos:exactMatch ex:RED"]
    assert [a["verdict"] for a in red["choices"][0]["actions"]] == ["adopt_mapping", "reject_mapping"]
    assert red["actions"][0]["rationale"] == \
        "No suitable term in outside ontologies; this stays Toy-only."
    blue = items["vocabulary", "definitions"][0]
    assert blue["editor"] == {"field": "description", "value": "", "placeholder": "Write the definition"}
    assert [a["label"] for a in blue["actions"]] == ["Save new definition", "Definition is fine"]
    signoff = items["schema", "signoff"]
    assert all(i["actions"][0]["verdict"] == "keep" for i in signoff)
    assert {i["group"] for i in signoff} >= {"things", "Other modules"}
    # keep-or-cut has no toy rows; its builder on its own:
    _, acts, _ = answers.keep_or_cut("term:TOY_0001", [], {}, answers.Ctx(bundle))
    assert [a["verdict"] for a in acts] == ["keep", "delete"]
    assert acts[1]["confirm"] == 'Delete "red"? It is removed from the schema when you apply.'


def test_answer_keys():
    a = answers.action("Accept", "adopt_mapping", "x", operation={"predicate": "p", "object": "o"},
                       evidence="map:1")
    b = answers.action("Other label", "adopt_mapping", "other rationale",
                       operation={"object": "o", "predicate": "p"}, evidence="map:1")
    assert a["answer"] == b["answer"]          # same decision, same key
    grades = [answers.action(g, "adopt_mapping", "x",
                             operation={"predicate": "p", "object": "o", "fit": g}, evidence="map:1")
              for g in ("exact", "broad")]
    assert grades[0]["answer"] != grades[1]["answer"]
    for x in (a, *grades):
        assert ANSWER_RE.match(x["answer"])


def test_reasons_config():
    bundle = {"subjects": {"subjects": {
        "term:A": {"kind": "term", "name": "A", "label": "alpha"},
        "term:B": {"kind": "term", "name": "B", "label": "beta"}}},
        "review_types": {"title": "P", "reasons": {
            "usage": {"pattern": r"has (?P<n>\d+) files", "text": "Used by {n:,} files.",
                      "else": "Used a lot."},
            "plain": {"text": "Not linked.", "hide_if": ["usage"]},
            "sim": {"pattern": r"similarity (?P<score>[\d.]+)", "text": 'Like "{other}" ({score}).'},
            "quiet": {"hide": True}}}}
    ctx = answers.Ctx(bundle)
    f = lambda rule, msg, subjects=("term:A",): {"rule": rule, "message": msg, "subjects": list(subjects)}  # noqa: E731
    assert answers.reasons_for([f("usage", "x has 12345 files"), f("plain", "m"), f("quiet", "q"),
                                f("raw", "raw text"), f("raw", "raw text")], "term:A", ctx) == \
        ["Used by 12,345 files.", "raw text"]
    assert answers.reasons_for([f("usage", "nothing"), f("plain", "m")], "term:A", ctx) == ["Used a lot."]
    assert answers.reasons_for([f("plain", "m")], "term:A", ctx) == ["Not linked."]
    assert answers.reasons_for([f("sim", "similarity 0.91", ("term:A", "term:B"))], "term:A", ctx) == \
        ['Like "beta" (0.91).']
    # A float-looking group becomes a float, so templates can format it.
    pct = answers.Ctx({**bundle, "review_types": {"title": "P", "reasons": {
        "sim": {"pattern": r"similarity (?P<score>[\d.]+)", "text": "{score:.0%} like \"{other}\"."}}}})
    assert answers.reasons_for([f("sim", "similarity 0.8527", ("term:A", "term:B"))], "term:A", pct) == \
        ['85% like "beta".']


def test_review_types_v1_rejected(toy):
    path = toy / "curation" / "review_types.yaml"
    path.write_text(path.read_text().replace("version: 2", "version: 1"))
    with pytest.raises(review.ReviewTypesError, match="version: 2"):
        review.build(Workbench.open(toy / "workbench.yaml"))
    path.write_text(path.read_text().replace("version: 1", "version: 2\nbogus: 1"))
    with pytest.raises(review.ReviewTypesError, match="bogus"):
        review.build(Workbench.open(toy / "workbench.yaml"))


def test_review_none_without_review_types(toy):
    (toy / "curation" / "review_types.yaml").unlink()
    wb = Workbench.open(toy / "workbench.yaml")
    bundle = snapshot.build(wb)
    assert derive.review(bundle, derive.status(bundle, []), 1) is None


def test_derive_imports_no_plugin(toy, monkeypatch):
    _, bundle = bundle_of(toy)

    def refuse(*a, **k):
        raise AssertionError("derive imported a module")
    monkeypatch.setattr(importlib, "import_module", refuse)
    real_import = builtins.__import__

    def guarded(name, *a, **k):
        if name not in ("collections",) and not name.startswith("curation_workbench") \
                and name.split(".")[0] not in ("json", "yaml", "re", "hashlib"):
            raise AssertionError(f"derive imported {name}")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", guarded)
    st = derive.status(bundle, [])
    assert derive.review(bundle, st, 1)["tracks"]
