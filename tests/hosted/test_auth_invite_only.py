"""Invite-only sign-in: every new account waits for a superadmin, except env-listed admins."""
import pytest
from .hostedtools import ADMIN_EMAIL
from sqlmodel import select

from curation_workbench.hosted.auth import on_user_created
from snapgate import UserStatus
from snapgate import settings as sg_settings
from snapgate.core import sign_in_oidc_user
from snapgate.useradmin import LoginEvent

pytestmark = pytest.mark.postgres


def oidc(db, email, sub):
    """What /auth/login/callback does with verified Prosopia claims, then our hook."""
    user, new = sign_in_oidc_user(db, {"sub": sub, "email": email, "name": email.split("@")[0],
                                       "email_verified": True}, issuer="https://prosopia.test")
    if new:
        on_user_created(user, db)
    db.refresh(user)
    return user


def test_first_ever_sign_in_is_pending(db):
    u = oidc(db, "first@example.org", "s1")      # snapgate would approve the first user
    assert u.status == UserStatus.PENDING and u.approved_at is None
    assert oidc(db, "second@example.org", "s2").status == UserStatus.PENDING


def test_env_superadmin_is_approved(db):
    assert oidc(db, ADMIN_EMAIL, "s1").status == UserStatus.APPROVED


def test_pending_user_waits_for_approval(client, db, make_user, auth_headers):
    admin = make_user(ADMIN_EMAIL)
    u = oidc(db, "newbie@example.org", "s9")
    r = client.get("/api/v1/me", headers=auth_headers(u))
    assert r.status_code == 403 and r.json()["code"] == "user_not_approved"
    r = client.get("/auth/admin/users?status=pending", headers=auth_headers(admin))
    assert [x["email"] for x in r.json()["users"]] == ["newbie@example.org"]
    r = client.post(f"/auth/admin/users/{u.id}/approve", headers=auth_headers(admin))
    assert r.status_code == 200, r.text
    r = client.get("/api/v1/me", headers=auth_headers(u))
    assert r.status_code == 200
    assert r.json() == {"user": {"id": u.id, "name": "newbie", "email": "newbie@example.org"},
                        "site_role": None, "memberships": {}}
    r = client.get("/auth/admin/users?status=approved", headers=auth_headers(admin))
    assert {x["email"] for x in r.json()["users"]} == {ADMIN_EMAIL, "newbie@example.org"}


def test_suspended_user_is_locked_out(client, make_user, auth_headers):
    admin, u = make_user(ADMIN_EMAIL), make_user("someone@example.org")
    assert client.get("/api/v1/me", headers=auth_headers(u)).status_code == 200
    r = client.post(f"/auth/admin/users/{u.id}/suspend", json={"reason": "test"},
                    headers=auth_headers(admin))
    assert r.status_code == 200, r.text
    r = client.get("/api/v1/me", headers=auth_headers(u))
    assert r.status_code == 403 and r.json()["code"] == "user_not_approved"


def test_demo_sign_in_goes_through_the_hooks(monkeypatch, engine, settings, db):
    """The router path: a new account is pending and its sign-in is logged as not allowed."""
    from fastapi.testclient import TestClient

    from curation_workbench.hosted.app import create_app
    monkeypatch.setattr(sg_settings, "DEMO_LOGIN", True)
    with TestClient(create_app(engine=engine, settings=settings)) as c:
        r = c.post("/auth/demo-login/user")
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
        r = c.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.json()["user"]["status"] == "pending"
        assert c.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    events = db.exec(select(LoginEvent)).all()
    assert [(e.email, e.allowed) for e in events] == [("user@demo.local", False)]
