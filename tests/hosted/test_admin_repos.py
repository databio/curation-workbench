"""A superadmin registers a project with its GitHub repo from the admin page
(`POST /api/v1/admin/projects` with `repo`), and fetches it again on demand.
GitHub is a fake here."""
import shutil

import pytest
from test_repos import SHA1, SHA2, TOY, FakeGitHub

from curation_workbench import snapshot
from curation_workbench.hosted.sync import github
from curation_workbench.workbench import Workbench

from .hostedtools import ADMIN_EMAIL

pytestmark = pytest.mark.postgres


@pytest.fixture
def gh(monkeypatch, tmp_path):
    root = tmp_path / "toy"
    shutil.copytree(TOY, root)
    fake = FakeGitHub("databio/toy")
    fake.bundle = snapshot.build(Workbench.open(root / "workbench.yaml"))
    monkeypatch.setattr(github, "GitHub", lambda: fake)
    return fake


def test_register_from_the_admin_page(client, make_user, auth_headers, gh):
    h = auth_headers(make_user(ADMIN_EMAIL))
    body = {"slug": "toy", "name": "Toy", "repo": "https://github.com/databio/toy", "branch": "master"}
    r = client.post("/api/v1/admin/projects", json=body, headers=h)
    assert r.status_code == 400 and "cannot read databio/toy" in r.json()["detail"]
    gh.put(SHA1, gh.bundle)
    r = client.post("/api/v1/admin/projects", json=body, headers=h)
    assert r.status_code == 201, r.text
    p = r.json()["project"]
    assert (p["repo"], p["branch"], p["commit"], p["last_error"]) == ("databio/toy", "master", SHA1, None)
    assert client.post("/api/v1/admin/projects", json=body, headers=h).status_code == 409
    assert client.get("/api/v1/projects/toy/review", headers=h).status_code == 200

    gh.put(SHA2, gh.bundle)
    r = client.post("/api/v1/admin/projects/toy/sync", headers=h)
    assert r.status_code == 200 and r.json()["fetch"]["published"] is True
    assert r.json()["project"]["commit"] == SHA2
    rows = client.get("/api/v1/admin/projects", headers=h).json()["projects"]
    assert [(x["slug"], x["repo"], x["commit"]) for x in rows] == [("toy", "databio/toy", SHA2)]


def test_bad_repo_is_refused(client, make_user, auth_headers, gh):
    h = auth_headers(make_user(ADMIN_EMAIL))
    r = client.post("/api/v1/admin/projects", headers=h,
                    json={"slug": "toy", "name": "Toy", "repo": "https://gitlab.com/a/b"})
    assert r.status_code == 400 and "not a GitHub repo" in r.json()["detail"]
