"""Who may call snapgate's /auth/admin/* routes: a site-admin browser session only."""
import pytest
from .hostedtools import ADMIN_EMAIL, SITE_ADMIN_EMAIL

pytestmark = pytest.mark.postgres

ROUTES = [
    ("GET", "/auth/admin/users", None),
    ("GET", "/auth/admin/users?status=approved", None),
    ("GET", "/auth/admin/users/{uid}", None),
    ("POST", "/auth/admin/users/{uid}/approve", None),
    ("POST", "/auth/admin/users/{uid}/suspend", {"reason": "test"}),
    ("POST", "/auth/admin/users/{uid}/unsuspend", None),
    ("GET", "/auth/admin/logins", None),
]

EXPECTED = {
    "anonymous": (401, None),
    "plain_session": (403, "not_admin"),
    "superadmin_key": (403, "session_required"),
    "admin_session": None,
    "superadmin_session": None,
}


@pytest.mark.parametrize("kind", sorted(EXPECTED))
def test_admin_routes(client, make_user, auth_headers, api_key_headers, kind):
    superadmin = make_user(ADMIN_EMAIL)
    headers = {
        "anonymous": lambda: {},
        "plain_session": lambda: auth_headers(make_user("plain@example.org")),
        "superadmin_key": lambda: api_key_headers(superadmin),
        "admin_session": lambda: auth_headers(make_user(SITE_ADMIN_EMAIL)),
        "superadmin_session": lambda: auth_headers(superadmin),
    }[kind]()
    for method, path, body in ROUTES:
        target = make_user(f"target-{method}-{len(path)}-{path[-6:]}@example.org".replace("/", "")
                           .replace("?", "").replace("=", ""))
        r = client.request(method, path.format(uid=target.id), json=body, headers=headers)
        expected = EXPECTED[kind]
        if expected is None:
            assert r.status_code not in (401, 403), (path, r.text)
            if method == "GET":
                assert r.status_code == 200, (path, r.text)
            continue
        status, code = expected
        assert r.status_code == status, (path, r.text)
        if code:
            assert r.json()["code"] == code, (path, r.text)
