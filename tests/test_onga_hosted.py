"""ONGA through the hosted path (plan 4 step 10.3), on ONGA_SCRATCH.

1. `cwb snapshot` + `cwb derive` with ONGA's decisions.yaml give the same
   status.json and review.json as `cwb regen` would write (rendered in memory;
   nothing in the scratch tree is written).
2. The tree as the worker sends it (the scratch worktree's files, as `git
   archive` would give them once committed, with the approved plugin tree
   overlaid at plugin_dir curation/plugin) goes through the runner in a child
   process with hosted=True, and derives the same files.
"""
import gzip
import io
import json
import subprocess
import tarfile

import pytest
from conftest import strip_generated

from curation_workbench import cli, derive, review, status
from curation_workbench.hosted.sync import gitrepo
from curation_workbench.hosted.sync.remote import LocalRunner
from curation_workbench.workbench import Workbench


@pytest.fixture(scope="module")
def regen(scratch):
    """What `cwb regen` writes for status.json and review.json (and status.json
    equals the committed one)."""
    wb = Workbench.open(scratch / "workbench.yaml")
    st = status.render(wb)[0]
    assert strip_generated(st) == strip_generated((scratch / "curation" / "status.json").read_text())
    return {"status.json": st, "review.json": review.render(wb)[0]}


def test_snapshot_derive_matches_regen(scratch, regen, tmp_path):
    bundle = tmp_path / "bundle.json"
    assert cli.main(["-c", str(scratch / "workbench.yaml"), "snapshot", "--out", str(bundle)]) == 0
    dest = tmp_path / "derived"
    assert cli.main(["derive", "--snapshot", str(bundle), "--decisions",
                     str(scratch / "curation" / "decisions.yaml"), "--out", str(dest)]) == 0
    for name in ("status.json", "review.json"):
        assert (dest / name).read_text() == regen[name], name


def tree_tar(root):
    """A tar of the worktree's files (tracked, plus untracked ones git does not
    ignore), like `git archive` of a commit that includes uncommitted work."""
    names = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
                           capture_output=True, check=True).stdout.decode().split("\0")
    files = {n: (root / n).read_bytes() for n in sorted(set(names))
             if n and (root / n).is_file() and not (root / n).is_symlink()}
    return _tar(files)


def plugin_tree(root):
    """The approved plugin tree: a gzip tar of curation/plugin/*.py."""
    pdir = root / "curation" / "plugin"
    return gzip.compress(_tar({p.name: p.read_bytes() for p in sorted(pdir.glob("*.py"))}))


def _tar(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_runner_hosted_matches_regen(scratch, regen):
    members = gitrepo.rewrite_tree(tree_tar(scratch), "curation/plugin", plugin_tree(scratch))
    bundle = LocalRunner(timeout_s=300).snapshot(members, "workbench.yaml", True)
    assert bundle["plugins"] == ["onga_plugin"]
    assert "the apply: section is ignored in hosted mode" in bundle["warnings"]
    decisions = bundle["repo_decisions"]
    st = derive.status(bundle, decisions)
    assert status.dump(st) == regen["status.json"]
    next_id = json.loads(regen["review.json"])["decisions_next_id"]  # the repo store's, as regen saw it
    assert review.dump(derive.review(bundle, st, next_id)) == regen["review.json"]
