"""The worker's git mirror, against a throwaway repo over file:// (test mode;
the production config refuses file:)."""
import gzip
import io
import tarfile

import pytest
from synctools import commit, git, make_repo, set_plugin_message

from curation_workbench.hosted.sync import gitrepo
from curation_workbench.hosted.sync.settings import SyncSettings
from curation_workbench.runner import Member


@pytest.fixture
def origin(tmp_path):
    return make_repo(tmp_path / "origin")


def mirror(tmp_path, origin, **kw):
    return gitrepo.GitRepo(tmp_path / "sync" / "repos" / "1.git", f"file://{origin}", "master",
                           sync_dir=tmp_path / "sync", allow_file=True, **kw)


def names(members):
    return {m.name for m in members}


def test_check_url():
    assert gitrepo.check_url("https://github.com/databio/onga")
    for bad in ("http://github.com/x/y", "https://evil.example/x", "file:///tmp/x", "/tmp/x",
                "https://user:pw@github.com/x/y", "git@github.com:x/y"):
        with pytest.raises(gitrepo.GitError):
            gitrepo.check_url(bad)
    assert gitrepo.check_url("file:///tmp/x", allow_file=True)


def test_file_protocol_refused_without_test_mode(tmp_path, origin):
    repo = gitrepo.GitRepo(tmp_path / "m.git", f"file://{origin}", "master", sync_dir=tmp_path)
    with pytest.raises(gitrepo.GitError):
        repo.head()


def test_head_fetch_archive(tmp_path, origin):
    repo = mirror(tmp_path, origin)
    head = repo.head()
    assert head == git(origin, "rev-parse", "HEAD")
    assert repo.fetch() == head
    assert not (repo.path / "workbench.yaml").exists()           # bare: nothing checked out
    (origin / ".github").mkdir()
    (origin / ".github" / "ci.yml").write_text("x")
    sha = commit(origin)
    assert repo.head() == sha and repo.fetch() == sha
    members = repo.archive(sha)
    got = names(members)
    assert "workbench.yaml" in got and "plugin/toy_plugin.py" in got
    assert not any(n.startswith(".git") for n in got)
    assert repo.show(sha, "workbench.yaml").startswith(b"# A tiny project")
    assert repo.show(sha, "nope.txt") is None


def test_plugin_tree_and_overlay(tmp_path, origin):
    repo = mirror(tmp_path, origin)
    first = repo.fetch()
    tree1 = repo.plugin_tree(first, "plugin")
    assert gitrepo.SHA_RE.match(tree1)
    assert repo.plugin_tree(first, "src/things.yaml") is None
    approved = repo.tree_tar_gz(tree1)
    second = set_plugin_message(origin, "CHANGED")
    repo.fetch()
    tree2 = repo.plugin_tree(second, "plugin")
    assert tree2 != tree1
    assert "CHANGED" in repo.diff(tree1, tree2)
    members = {m.name: m for m in repo.archive(second, "plugin", approved)}
    assert b"CHANGED" not in members["plugin/toy_plugin.py"].data      # approved files, not head's
    plain = {m.name: m for m in repo.archive(second, "plugin", None)}
    assert "plugin/toy_plugin.py" not in plain                          # no overlay: dropped
    with pytest.raises(gitrepo.GitError):
        repo.tree_tar_gz("0" * 40)


def _tar(entries):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tar:
        for name, kind, data in entries:
            info = tarfile.TarInfo(name)
            if kind == "sym":
                info.type, info.linkname = tarfile.SYMTYPE, data
                tar.addfile(info)
            elif kind == "link":
                info.type, info.linkname = tarfile.LNKTYPE, data
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_rewrite_rejects_escapes():
    ok = gitrepo.rewrite_tree(_tar([("a/b.txt", "f", b"x"), ("a/link", "sym", "b.txt"),
                                     (".gitignore", "f", b"x")]))
    assert {m.name for m in ok} == {"a/b.txt", "a/link"}
    for entries in ([("a/link", "sym", "../../etc/passwd")], [("a/link", "sym", "/etc/passwd")],
                    [("../x", "f", b"x")], [("/abs", "f", b"x")], [("h", "link", "a")]):
        with pytest.raises(gitrepo.GitError):
            gitrepo.rewrite_tree(_tar(entries))
    with pytest.raises(gitrepo.GitError) as e:
        gitrepo.rewrite_tree(_tar([("big", "f", b"x" * 100)]), max_bytes=10)
    assert e.value.code == "too_large"


def test_rewrite_overlay_replaces_plugin_dir():
    overlay = gzip.compress(_tar([("p.py", "f", b"approved")]))
    out = gitrepo.rewrite_tree(_tar([("plug/p.py", "f", b"head"), ("plug/extra.py", "f", b"new"),
                                     ("x.txt", "f", b"x")]), "plug", overlay)
    files = {m.name: m.data for m in out if m.kind == "file"}
    assert files == {"plug/p.py": b"approved", "x.txt": b"x"}
    assert Member("plug", "dir", None) in out


def test_for_resident_uses_settings(tmp_path):
    s = SyncSettings(sync_dir=tmp_path, allow_file_repos=True)
    repo = gitrepo.for_resident(s, {"id": 7, "repo_url": "file:///x", "branch": "main"})
    assert repo.path == tmp_path / "repos" / "7.git" and repo.allow_file
    with pytest.raises(gitrepo.GitError):
        gitrepo.for_resident(s, {"id": 7, "repo_url": "file:///x", "branch": "bad..name"})
