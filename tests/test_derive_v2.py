"""derive.review: the review.json the bundle and status give equals
`review.build` for the toy, and derive runs no plugin code at all.

(The review UI plan upgrades review.json to version 2; until then derive.review
produces today's format, and this test pins it to `review.build`.)"""
import builtins
import importlib
import json
import shutil
from pathlib import Path

import pytest

from curation_workbench import derive, review, snapshot
from curation_workbench.workbench import Workbench

TOY = Path(__file__).parent / "fixtures" / "toy"
REVIEW_TYPES = """version: 1
tracks:
  - id: vocabulary
    title: Vocabulary
    kinds: [term, enum]
    types:
      - {id: links, title: Links, rules: [unreviewed-mapping, no-mapping-at-all]}
      - {id: signoff, title: Sign off, fallback: true}
  - id: schema
    title: Schema
    kinds: [class, slot, module, subset, usage]
    types:
      - {id: fields, title: Fields, rules: [required-policy]}
      - {id: signoff, title: Sign off, fallback: true}
"""


@pytest.fixture
def toy(tmp_path):
    root = tmp_path / "toy"
    shutil.copytree(TOY, root)
    (root / "curation" / "review_types.yaml").write_text(REVIEW_TYPES)
    return root


def test_review_equals_review_build(toy):
    wb = Workbench.open(toy / "workbench.yaml")
    bundle = json.loads(snapshot.dumps(snapshot.build(wb)))
    st = derive.status(bundle, [])
    got = derive.review(bundle, st, 1)
    assert got == review.build(wb)
    counts = {t["id"]: {ty["id"]: ty["count"] for ty in t["types"]} for t in got["tracks"]}
    assert counts["schema"]["fields"] == 1 and counts["vocabulary"]["links"] >= 1


def test_review_none_without_review_types(toy):
    (toy / "curation" / "review_types.yaml").unlink()
    wb = Workbench.open(toy / "workbench.yaml")
    bundle = snapshot.build(wb)
    assert derive.review(bundle, derive.status(bundle, []), 1) is None


def test_derive_imports_no_plugin(toy, monkeypatch):
    wb = Workbench.open(toy / "workbench.yaml")
    bundle = json.loads(snapshot.dumps(snapshot.build(wb)))

    def refuse(*a, **k):
        raise AssertionError("derive imported a module")
    monkeypatch.setattr(importlib, "import_module", refuse)
    real_import = builtins.__import__

    def guarded(name, *a, **k):
        if name not in ("collections",) and not name.startswith("curation_workbench") \
                and name.split(".")[0] not in ("json", "yaml"):
            raise AssertionError(f"derive imported {name}")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", guarded)
    st = derive.status(bundle, [])
    assert derive.review(bundle, st, 1)["tracks"]
