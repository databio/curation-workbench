"""review.json version 2 items equal the ONGA site's own `itemsFor()`.

tests/onga_dump_items.ts runs ONGA's site/src/lib/review-types.ts (read-only,
with `npx tsx`, cwd $ONGA_SCRATCH/site) for every track and type; this test
compares each item field by field with cwb's (`href` excluded; `browse`
compared as a path suffix; fields only cwb has are ignored). Both run against
the same disposable worktree so the decision state matches. Skips without
ONGA_SCRATCH, npx or the uncommitted review-types.ts in the worktree.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import ADAPTER

from curation_workbench import review
from curation_workbench.workbench import Workbench

DUMP = Path(__file__).parent / "onga_dump_items.ts"
ACTION_KEYS = ("label", "verdict", "operation", "rationale", "evidence", "also", "tone", "input", "confirm")


def ts_items(root):
    if not shutil.which("npx"):
        pytest.skip("npx is not installed")
    if not (root / "site" / "src" / "lib" / "review-types.ts").exists():
        pytest.skip("the ONGA worktree has no site/src/lib/review-types.ts (copy it in; see README)")
    out = subprocess.run(["npx", "--yes", "tsx", str(DUMP), str(root)], cwd=root / "site",
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)


def norm_action(a):
    return {k: a.get(k) for k in ACTION_KEYS}


def norm_ts(it):
    return {
        "sid": it["sid"], "kind": it["kind"], "title": it["title"],
        "container": it.get("container"), "container_label": it.get("containerLabel"),
        "description": it["description"], "reasons": it["reasons"], "findings": it["findings"],
        "choices": [{"text": c["text"], "detail": c.get("detail"),
                     "actions": [norm_action(a) for a in c["actions"]]} for c in it["choices"]],
        "actions": [norm_action(a) for a in it["actions"]],
        "editor": it.get("editor"), "hash": it["hash"], "score": it["score"],
    }


def norm_py(it):
    out = norm_ts({**it, "containerLabel": it["container_label"]})
    return out


def test_items_match_onga(scratch):
    want = ts_items(scratch)
    wb = Workbench.open(ADAPTER, root=scratch)
    data = review.build(wb)
    got = {f"{t['id']}/{ty['id']}": ty["items"] for t in data["tracks"] for ty in t["types"]}
    assert sorted(got) == sorted(want)
    for key in want:
        a, b = want[key], got[key]
        assert [x["sid"] for x in a] == [x["sid"] for x in b], key
        for x, y in zip(a, b):
            assert norm_ts(x) == norm_py(y), (key, x["sid"])
            if x.get("browse") is None:
                assert y["browse"] is None, (key, x["sid"])
            else:
                assert y["browse"] and y["browse"].endswith(x["browse"]), (key, x["sid"], y["browse"])


def test_answer_keys(scratch):
    wb = Workbench.open(ADAPTER, root=scratch)
    data = review.build(wb)
    import re
    from curation_workbench.store import ANSWER_RE
    seen = {}
    for t in data["tracks"]:
        for ty in t["types"]:
            for it in ty["items"]:
                acts = [a for c in it["choices"] for a in c["actions"]] + it["actions"]
                for a in acts:
                    assert ANSWER_RE.match(a["answer"]) and re.fullmatch(r"[0-9a-f]{12}", a["answer"])
                    sig = json.dumps({k: a[k] for k in ("verdict", "operation", "also", "evidence")},
                                     sort_keys=True)
                    assert seen.setdefault(a["answer"], sig) == sig
                keys = [a["answer"] for a in acts]
                assert len(keys) == len(set(keys)), it["sid"]
