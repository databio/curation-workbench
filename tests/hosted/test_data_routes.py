"""Read routes return exactly `views.<fn>(...)` of the published data, and the
same body as the local daemon (`cwb serve`) for the toy project at the same
decisions."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from .hostedtools import add_repo_decision, publish

from curation_workbench import derive, server, views
from curation_workbench.hosted import project_data
from curation_workbench.workbench import Workbench

pytestmark = pytest.mark.postgres

T = "/api/v1/projects/toy"


@pytest.fixture
def curator(team, auth_headers):
    return auth_headers(team.curator)


@pytest.fixture
def local(toy_root):
    """The local daemon on the same toy copy, on a free loopback port."""
    wb = Workbench.open(toy_root / "workbench.yaml")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(wb, "tester", None))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def get(path):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                     headers={"Host": f"127.0.0.1:{port}"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    yield get
    httpd.shutdown()


def test_not_synced_before_a_snapshot(client, make_project, make_user, auth_headers):
    from curation_workbench.hosted.service import projects, roles
    u = make_user("c@example.org")
    make_project("bare")
    projects.set_member(client.app.state.engine, roles.OPERATOR, "bare", u.id, "curator")
    h = auth_headers(u)
    for path in ("/review", "/subject_index", "/findings", "/history", "/pending"):
        r = client.get(f"/api/v1/projects/bare{path}", headers=h)
        assert r.status_code == 503 and r.json()["code"] == "not_synced", path
    r = client.get("/api/v1/projects/bare", headers=h)
    assert r.status_code == 200 and r.json()["commit"] is None and r.json()["synced_at"] is None
    assert client.get("/api/v1/projects/bare/decisions", headers=h).status_code == 200
    r = client.put("/api/v1/projects/bare/votes", headers=h, json={"sid": "x", "review_type": "subject",
                                                                     "answer": "keep"})
    assert r.status_code == 503


def test_routes_equal_views_and_local_daemon(client, engine, curator, local, toy_root):
    add_repo_decision(toy_root)
    publish(engine, "toy", toy_root)
    with engine.connect() as conn:
        pd = project_data.load(conn, "toy")
    records = derive.merge_decisions(pd.bundle["repo_decisions"], [])
    sid = "term:TOY_0001"
    expect = {
        "/review": views.review_index(pd.review),
        "/review/vocabulary/links": views.review_type_page(pd.review, "vocabulary", "links"),
        "/subject_index": views.subject_index(pd.bundle, pd.status),
        f"/subject?sid={sid}": {**views.subject_view(pd.bundle, pd.status, records, sid), "votes": []},
        "/verdicts": views.verdicts_view(pd.bundle["verdicts"]),
        "/findings": views.findings_view(pd.bundle, pd.status),
        "/history": views.history(records, pd.bundle),
    }
    for path, body in expect.items():
        r = client.get(T + path, headers=curator)
        assert r.status_code == 200, (path, r.text)
        assert r.json() == json.loads(json.dumps(body)), path
        mine = r.json()
        theirs = local("/api" + path)
        if path.startswith("/subject?"):
            mine.pop("votes")
        if path.startswith("/review"):   # the next id is the store's own: DEC-0002 vs H-0001
            assert mine.pop("decisions_next_id") == "H-0001"
            assert theirs.pop("decisions_next_id") == "DEC-0002"
        assert mine == theirs, path
    assert [h["id"] for h in client.get(T + "/history", headers=curator).json()["history"]] == ["DEC-0001"]
    r = client.get(T + "/pending", headers=curator).json()
    assert r == {"decisions": [], "errors": {}, "ok": True}       # repo decisions are not hosted ones
    assert "files" not in r
    assert client.get(T + "/subject?sid=term:NOPE", headers=curator).status_code == 404
    assert client.get(T + "/review/nope/nope", headers=curator).status_code == 404


def test_new_snapshot_is_picked_up(client, engine, curator, toy_root):
    before = client.get("/api/v1/projects/toy", headers=curator).json()
    src = toy_root / "src" / "things.yaml"
    src.write_text(src.read_text().replace("description:", "description: Changed.", 1)
                   if "description:" in src.read_text() else src.read_text() + "\n")
    add_repo_decision(toy_root)
    publish(engine, "toy", toy_root)
    after = client.get("/api/v1/projects/toy", headers=curator).json()
    assert after["commit"] != before["commit"]
    assert [h["id"] for h in client.get(T + "/history", headers=curator).json()["history"]] == ["DEC-0001"]


def test_hosted_decisions_in_history_and_pending(client, curator, toy_hash):
    from .hostedtools import decision_body
    r = client.post(T + "/decisions", json=decision_body(toy_hash), headers=curator)
    assert r.status_code == 201
    hist = client.get(T + "/history", headers=curator).json()["history"]
    assert [h["id"] for h in hist] == ["H-0001"] and hist[0]["plugin_errors"] == []
    pend = client.get(T + "/pending", headers=curator).json()
    assert [d["id"] for d in pend["decisions"]] == ["H-0001"] and pend["decisions"][0]["effects"] is None
    subj = client.get(T + "/subject?sid=term:TOY_0001", headers=curator).json()
    assert [d["id"] for d in subj["decisions"]] == ["H-0001"]
