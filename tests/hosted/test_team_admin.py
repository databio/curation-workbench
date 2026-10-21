"""Team and project admin: superadmin session only; approve first, then add."""
import pytest
from .hostedtools import ADMIN_EMAIL

from snapgate import UserStatus

pytestmark = pytest.mark.postgres

A = "/api/v1/admin/projects"


def test_add_change_remove_member(client, toy_project, make_user, auth_headers):
    admin, u = make_user(ADMIN_EMAIL), make_user("p@example.org", name="Pat")
    h = auth_headers(admin)
    r = client.put(f"{A}/toy/team/{u.id}", json={"role": "voter"}, headers=h)
    assert r.status_code == 200 and r.json()["member"]["role"] == "voter"
    assert client.get("/api/v1/me", headers=auth_headers(u)).json()["memberships"] == {"toy": "voter"}
    client.put(f"{A}/toy/team/{u.id}", json={"role": "curator"}, headers=h)
    r = client.get("/api/v1/projects/toy/team", headers=auth_headers(u))
    assert r.json() == {"members": [{"user_id": u.id, "name": "Pat", "email": "p@example.org",
                                     "role": "curator"}]}
    assert client.delete(f"{A}/toy/team/{u.id}", headers=h).status_code == 204
    assert client.get("/api/v1/projects/toy", headers=auth_headers(u)).status_code == 404
    assert client.delete(f"{A}/toy/team/{u.id}", headers=h).status_code == 404
    r = client.put(f"{A}/toy/team/{u.id}", json={"role": "voter"}, headers=h)   # back again
    assert r.status_code == 200
    assert client.get("/api/v1/projects/toy", headers=auth_headers(u)).json()["role"] == "voter"


def test_pending_account_cannot_be_added(client, toy_project, make_user, auth_headers):
    admin, u = make_user(ADMIN_EMAIL), make_user("w@example.org", status=UserStatus.PENDING)
    r = client.put(f"{A}/toy/team/{u.id}", json={"role": "voter"}, headers=auth_headers(admin))
    assert r.status_code == 409 and r.json()["code"] == "account_not_approved"
    r = client.put(f"{A}/toy/team/9999", json={"role": "voter"}, headers=auth_headers(admin))
    assert r.status_code == 404
    r = client.put(f"{A}/toy/team/{admin.id}", json={"role": "boss"}, headers=auth_headers(admin))
    assert r.status_code == 422


def test_project_admin(client, make_user, auth_headers):
    h = auth_headers(make_user(ADMIN_EMAIL))
    r = client.post(A, json={"slug": "onga", "name": "ONGA"}, headers=h)
    assert r.status_code == 201 and r.json()["project"]["slug"] == "onga"
    r = client.post(A, json={"slug": "onga", "name": "again"}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "conflict"
    for bad in ("Bad Slug", "x", "-lead"):
        r = client.post(A, json={"slug": bad, "name": "n"}, headers=h)
        assert r.status_code == 400 and r.json()["code"] == "invalid", bad
    assert client.patch(f"{A}/onga", json={"name": "ONGA 2"}, headers=h).json()["project"]["name"] == "ONGA 2"
    assert client.get("/api/v1/projects", headers=h).json()["projects"][0]["commit"] is None
    assert client.delete(f"{A}/onga", headers=h).json()["project"]["archived_at"]
    assert client.get("/api/v1/projects", headers=h).json() == {"projects": []}
    rows = client.get(A, headers=h).json()["projects"]
    assert [p["slug"] for p in rows] == ["onga"] and rows[0]["archived_at"]
    assert client.get("/api/v1/projects/onga", headers=h).status_code == 404


def test_only_superadmin_session(client, toy_project, make_user, auth_headers, api_key_headers):
    from .hostedtools import SITE_ADMIN_EMAIL
    site_admin = make_user(SITE_ADMIN_EMAIL)       # snapgate "admin": user admin only
    r = client.post(A, json={"slug": "zz", "name": "Z"}, headers=auth_headers(site_admin))
    assert r.status_code == 403 and r.json()["code"] == "not_admin"
    from curation_workbench.hosted.scopes import ALL_SCOPES
    r = client.get(A, headers=api_key_headers(make_user(ADMIN_EMAIL), ALL_SCOPES))
    assert r.status_code == 403 and r.json()["code"] == "session_required"


def test_cli_add_member(engine, toy_project, make_user, capsys, monkeypatch):
    from curation_workbench import cli
    u = make_user("cli@example.org", name="Cli")
    monkeypatch.setenv("CWB_DATABASE_URL", engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr("curation_workbench.hosted.cli._engine", lambda: engine)
    assert cli.main(["hosted", "admin", "add-member", "--project", "toy", "--email",
                     "CLI@example.org", "--role", "curator"]) == 0
    assert "Cli <cli@example.org> is a curator on toy" in capsys.readouterr().out
    p = make_user("pend@example.org", status=UserStatus.PENDING)
    with pytest.raises(SystemExit, match="account_not_approved"):
        cli.main(["hosted", "admin", "add-member", "--project", "toy", "--email", p.email,
                  "--role", "voter"])
