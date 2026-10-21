"""ONGA through the hosted path (plan 4 step 10.3), on ONGA_SCRATCH.

1. `cwb snapshot` + `cwb derive` with ONGA's decisions.yaml give the same
   status.json and review.json as `cwb regen` would write (rendered in memory;
   nothing in the scratch tree is written).
2. The tree as the worker sends it (`git archive` of the scratch worktree's
   HEAD, rewritten, with the adapter laid out the way plan 6 puts it in ONGA:
   workbench.yaml at the root, plugin_dir curation/plugin) goes through the
   runner in a child process with hosted=True, and derives the same files.
"""
import gzip
import io
import subprocess
import tarfile

import pytest
from conftest import ADAPTER, REPO, strip_generated

from curation_workbench import cli, derive, review, status
from curation_workbench.hosted.sync import gitrepo
from curation_workbench.hosted.sync.remote import LocalRunner
from curation_workbench.runner import Member
from curation_workbench.workbench import Workbench


@pytest.fixture(scope="module")
def regen(scratch):
    """What `cwb regen` writes for status.json and review.json (and status.json
    equals ONGA's own, see test_onga_status)."""
    wb = Workbench.open(ADAPTER, root=scratch)
    st = status.render(wb)[0]
    assert strip_generated(st) == strip_generated((scratch / "curation" / "status.json").read_text())
    return {"status.json": st, "review.json": review.render(wb)[0]}


def test_snapshot_derive_matches_regen(scratch, regen, tmp_path):
    bundle = tmp_path / "bundle.json"
    assert cli.main(["-c", str(ADAPTER), "--root", str(scratch),
                     "snapshot", "--out", str(bundle)]) == 0
    dest = tmp_path / "derived"
    assert cli.main(["derive", "--snapshot", str(bundle), "--decisions",
                     str(scratch / "curation" / "decisions.yaml"), "--out", str(dest)]) == 0
    for name in ("status.json", "review.json"):
        assert (dest / name).read_text() == regen[name], name


def plan6_layout(members):
    """The adapter files placed the way plan 6 moves them into ONGA."""
    cfg = ADAPTER.read_text().replace("root: ../../../onga", "root: .")
    cfg = cfg.replace("review_types: review_types.yaml", "review_types: curation/review_types.yaml")
    cfg += "plugin_dir: curation/plugin\n"
    adapter = REPO / "adapters" / "onga"
    drop = {"workbench.yaml", "curation/review_types.yaml"}
    return [m for m in members if m.name not in drop] + [
        Member("workbench.yaml", "file", cfg.encode()),
        Member("curation/review_types.yaml", "file", (adapter / "review_types.yaml").read_bytes()),
    ], gzip.compress(_tar({"onga_plugin.py": (adapter / "onga_plugin.py").read_bytes()}))


def _tar(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_runner_hosted_matches_regen(scratch, regen):
    raw = subprocess.run(["git", "-C", str(scratch), "archive", "--format=tar", "HEAD"],
                         capture_output=True, check=True).stdout
    members, plugin = plan6_layout(gitrepo.rewrite_tree(raw))
    members = gitrepo.rewrite_tree(_members_tar(members), "curation/plugin", plugin)
    bundle = LocalRunner(timeout_s=300).snapshot(members, "workbench.yaml", True)
    assert bundle["plugins"] == ["onga_plugin"]
    assert "the apply: section is ignored in hosted mode" in bundle["warnings"]
    decisions = bundle["repo_decisions"]
    st = derive.status(bundle, decisions)
    assert status.dump(st) == regen["status.json"]
    assert review.dump(derive.review(bundle, st)) == regen["review.json"]


def _members_tar(members):
    from curation_workbench.runner import write_members
    return write_members(members)
