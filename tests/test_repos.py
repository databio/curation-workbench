"""Project repos: the site reads a repo's committed snapshot over HTTPS (a fake
GitHub here, never the real one), registers projects, publishes new commits,
keeps the last good snapshot on errors, and never runs project code."""
import json
import shutil
from pathlib import Path

import pytest

from curation_workbench import snapshot
from curation_workbench.hosted.sync import github, repos
from curation_workbench.workbench import Workbench

TOY = Path(__file__).parent / "fixtures" / "toy"
SHA1, SHA2 = "1" * 40, "2" * 40


class FakeGitHub:
    """{(repo, sha, path): bytes} plus a branch head; counts calls."""

    def __init__(self, repo="databio/toy"):
        self.repo, self.heads, self.files, self.calls = repo, {}, {}, []

    def put(self, sha, bundle, branch="master", path="curation/snapshot.json"):
        self.heads[branch] = sha
        self.files[(sha, "workbench.yaml")] = f"project: toy\nsnapshot: {path}\n".encode()
        self.files[(sha, path)] = snapshot.dump(bundle).encode()

    def head(self, repo, branch):
        self.calls.append(("head", repo, branch))
        if repo != self.repo or branch not in self.heads:
            raise github.FetchError(f"GET .../{repo}/commits/{branch}: HTTP 404")
        return self.heads[branch]

    def file(self, repo, sha, path, limit=None):
        self.calls.append(("file", sha, path))
        if (sha, path) not in self.files:
            raise github.FetchError(f"GET .../{repo}/{sha}/{path}: HTTP 404")
        return self.files[(sha, path)]


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "toy"
    shutil.copytree(TOY, root)
    return snapshot.build(Workbench.open(root / "workbench.yaml"))


def test_parse_repo():
    for v in ("databio/onga", "https://github.com/databio/onga", "https://github.com/databio/onga.git",
              "github.com/databio/onga/"):
        assert github.parse_repo(v) == "databio/onga"
    for bad in ("", "onga", "https://gitlab.com/a/b", "a/b/c", "../x/y"):
        with pytest.raises(ValueError):
            github.parse_repo(bad)
    assert github.check_branch("master") == "master"
    for bad in ("", "a..b", "/x", "x/"):
        with pytest.raises(ValueError):
            github.check_branch(bad)


def test_snapshot_path_must_stay_in_the_repo():
    assert github.snapshot_path({"snapshot": "curation/snapshot.json"}) == "curation/snapshot.json"
    for bad in ({}, {"snapshot": ""}, {"snapshot": "../x.json"}, {"snapshot": "/etc/passwd"},
                {"snapshot": "a/../../x"}):
        with pytest.raises(github.FetchError):
            github.snapshot_path(bad)


def test_fetch_snapshot(bundle):
    gh = FakeGitHub()
    gh.put(SHA1, bundle)
    sha, got = github.fetch_snapshot(gh, "databio/toy", "master")
    assert sha == SHA1 and got == json.loads(json.dumps(bundle))
    gh.files[(SHA1, "curation/snapshot.json")] = b'{"format": "nope"}'
    with pytest.raises(github.FetchError, match="not a cwb-snapshot/1 bundle"):
        github.fetch_snapshot(gh, "databio/toy", "master")


@pytest.mark.postgres
def test_register_sync_and_errors(pg_engine, bundle):
    from curation_workbench.hosted import project_data
    from curation_workbench.hosted.service import roles
    from curation_workbench.store import StoreError
    project_data.clear_cache()
    gh = FakeGitHub()
    with pytest.raises(StoreError, match="cannot read"):
        repos.register(pg_engine, roles.OPERATOR, "toy", "Toy", "databio/toy", "master", gh=gh)
    gh.put(SHA1, bundle)
    out = repos.register(pg_engine, roles.OPERATOR, "toy", "Toy", "https://github.com/databio/toy",
                         "master", gh=gh)
    assert out["repo"]["repo"] == "databio/toy" and out["repo"]["commit"] == SHA1
    assert out["repo"]["last_error"] is None
    with pg_engine.connect() as conn:
        pd = project_data.load(conn, "toy")
    assert pd.commit == SHA1 and pd.repo == "databio/toy" and pd.review is not None

    gh.calls.clear()                                 # same head: one cheap check, no download
    assert repos.sync_project(pg_engine, "toy", gh=gh) == {"commit": SHA1, "published": False,
                                                           "error": None}
    assert gh.calls == [("head", "databio/toy", "master")]

    gh.heads["master"] = SHA2                        # a new commit with no snapshot file
    gh.files[(SHA2, "workbench.yaml")] = b"project: toy\n"
    r = repos.sync_project(pg_engine, "toy", gh=gh)
    assert not r["published"] and "sets no `snapshot:` path" in r["error"]
    with pg_engine.connect() as conn:
        info = repos.repo_info(conn, pd.project["id"])
        assert project_data.load(conn, "toy").commit == SHA1     # last good snapshot stays live
    assert info["last_error"] == r["error"]

    gh.put(SHA2, bundle)                             # fixed: the next poll publishes it
    assert repos.sync_all(pg_engine, gh=gh) == {"toy": {"commit": SHA2, "published": True,
                                                        "error": None}}
    rows = repos.list_repos(pg_engine, roles.OPERATOR)
    assert [(x["slug"], x["commit"], x["last_error"]) for x in rows] == [("toy", SHA2, None)]


@pytest.mark.postgres
def test_dev_repos_are_never_fetched(pg_engine, bundle):
    from curation_workbench.hosted.cli import dev_snapshot
    from curation_workbench.store.pg import projects
    projects.create_project(pg_engine, "toy", "Toy")
    dev_snapshot(pg_engine, "toy", bundle)
    gh = FakeGitHub()
    assert repos.sync_all(pg_engine, gh=gh) == {}
    assert repos.sync_project(pg_engine, "toy", gh=gh)["published"] is False
    assert gh.calls == []
