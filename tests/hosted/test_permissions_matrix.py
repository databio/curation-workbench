"""Every /api/v1 route against every kind of caller.

Expected: anonymous 401; a non-member 404 on project routes; a voter 403
`not_voter`/`not_curator` where it may not act; a key without
`curation:decide` 403 `insufficient_scope` on decision writes; a superadmin
session reads every project but votes and decides only on its own team;
admin routes 403 for every key (`session_required`) and every non-superadmin
session (`not_admin`).
"""
import pytest
from .hostedtools import decision_body, vote_body

from curation_workbench.hosted.scopes import ALL_SCOPES, DEFAULT_SCOPES

pytestmark = pytest.mark.postgres

T = "/api/v1/projects/toy"
SID = "term:TOY_0001"


def routes(h, uid):
    """(group, method, path, body, headers) in an order that keeps state usable."""
    q = f"sid={SID}&review_type=vocabulary/links"
    return [
        ("me", "GET", "/api/v1/me", None, {}),
        ("me", "GET", "/api/v1/projects", None, {}),
        ("read", "GET", T, None, {}),
        ("read", "GET", T + "/team", None, {}),
        ("read", "GET", T + "/review", None, {}),
        ("read", "GET", T + "/review/vocabulary/links", None, {}),
        ("read", "GET", T + "/subject_index", None, {}),
        ("read", "GET", T + f"/subject?sid={SID}", None, {}),
        ("read", "GET", T + "/verdicts", None, {}),
        ("read", "GET", T + "/findings", None, {}),
        ("read", "GET", T + "/history", None, {}),
        ("read", "GET", T + "/pending", None, {}),
        ("read", "GET", T + "/decisions", None, {}),
        ("read", "GET", T + "/decisions/H-0001", None, {}),
        ("read", "GET", T + "/decisions/export", None, {}),
        ("read", "GET", T + "/questions", None, {}),
        ("read", "GET", T + f"/votes?{q}", None, {}),
        ("vote", "PUT", T + "/votes", vote_body(h, SID, "vocabulary/links"), {}),
        ("vote", "DELETE", T + f"/votes?{q}", None, {}),
        ("decide", "POST", T + "/decisions", decision_body(h, "term:TOY_0002"), {}),
        ("decide", "PUT", T + "/decisions/H-0001", decision_body(h, rationale="changed"),
         {"If-Match": "1"}),
        ("decide", "POST", T + "/questions/resolve",
         {"sid": "enum:Color", "review_type": "vocabulary/links", "outcome": "no_action"}, {}),
        ("decide", "POST", T + "/questions/reopen",
         {"sid": "enum:Color", "review_type": "vocabulary/links"}, {}),
        ("decide", "DELETE", T + "/decisions/H-0001", None, {"If-Match": "2"}),
        ("admin", "GET", "/api/v1/admin/projects", None, {}),
        ("admin", "POST", "/api/v1/admin/projects", {"slug": "newp", "name": "New"}, {}),
        ("admin", "PATCH", "/api/v1/admin/projects/other", {"name": "Other 2"}, {}),
        ("admin", "PUT", f"/api/v1/admin/projects/toy/team/{uid}", {"role": "voter"}, {}),
        ("admin", "DELETE", f"/api/v1/admin/projects/toy/team/{uid}", None, {}),
        ("admin", "GET", "/api/v1/admin/residents", None, {}),
        ("admin", "GET", "/api/v1/admin/residents/toy", None, {}),
        ("admin", "DELETE", "/api/v1/admin/projects/other", None, {}),
    ]


ALLOW = "allow"
# caller kind -> group -> expected (ALLOW, or (status, code))
NOT_ADMIN = (403, "not_admin")
SESSION = (403, "session_required")
NF = (404, "not_found")
EXPECTED = {
    "anonymous": {g: (401, None) for g in ("me", "read", "vote", "decide", "admin")},
    "non_member": {"me": ALLOW, "read": NF, "vote": NF, "decide": NF, "admin": NOT_ADMIN},
    "voter_session": {"me": ALLOW, "read": ALLOW, "vote": ALLOW, "decide": (403, "not_curator"),
                      "admin": NOT_ADMIN},
    "curator_session": {"me": ALLOW, "read": ALLOW, "vote": ALLOW, "decide": ALLOW,
                        "admin": NOT_ADMIN},
    "curator_key_read_vote": {"me": ALLOW, "read": ALLOW, "vote": ALLOW,
                              "decide": (403, "insufficient_scope"), "admin": SESSION},
    "curator_key_decide": {"me": ALLOW, "read": ALLOW, "vote": ALLOW, "decide": ALLOW,
                           "admin": SESSION},
    "superadmin_session": {"me": ALLOW, "read": ALLOW, "vote": (403, "not_voter"),
                           "decide": (403, "not_curator"), "admin": ALLOW},
    "superadmin_key": {"me": ALLOW, "read": NF, "vote": NF, "decide": NF, "admin": SESSION},
}


@pytest.mark.parametrize("kind", sorted(EXPECTED))
def test_matrix(client, team, toy_hash, make_project, make_user, auth_headers, api_key_headers,
                kind):
    make_project("other")
    spare = make_user("spare@example.org")
    # A decision to read, edit and withdraw.
    r = client.post(T + "/decisions", json=decision_body(toy_hash), headers=auth_headers(team.curator))
    assert r.status_code == 201, r.text
    headers = {
        "anonymous": lambda: {},
        "non_member": lambda: auth_headers(team.outsider),
        "voter_session": lambda: auth_headers(team.voter),
        "curator_session": lambda: auth_headers(team.curator),
        "curator_key_read_vote": lambda: api_key_headers(team.curator, DEFAULT_SCOPES),
        "curator_key_decide": lambda: api_key_headers(team.curator, ALL_SCOPES),
        "superadmin_session": lambda: auth_headers(team.admin),
        "superadmin_key": lambda: api_key_headers(team.admin, ALL_SCOPES),
    }[kind]()
    for group, method, path, body, extra in routes(toy_hash, spare.id):
        r = client.request(method, path, json=body, headers={**headers, **extra})
        want = EXPECTED[kind][group]
        where = f"{kind} {method} {path}: {r.status_code} {r.text[:200]}"
        if want == ALLOW:
            assert r.status_code < 400, where
            continue
        status, code = want
        assert r.status_code == status, where
        if code:
            assert r.json()["code"] == code, where
