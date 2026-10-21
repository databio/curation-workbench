"""Read routes return exactly `views.<fn>(...)` of the published data; the
Approved page's body; and hosted decisions that reach the repo get marked
applied on the next sync."""
import json

import pytest
from .hostedtools import add_repo_decision, decision_body, publish

from curation_workbench import derive, views
from curation_workbench.hosted import project_data
from curation_workbench.store import YamlStore, landing
from curation_workbench.workbench import Workbench

pytestmark = pytest.mark.postgres

T = "/api/v1/projects/toy"


@pytest.fixture
def curator(team, auth_headers):
    return auth_headers(team.curator)


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


def test_routes_equal_views(client, engine, curator, toy_root):
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
    page = client.get(T + "/review/vocabulary/links", headers=curator).json()
    assert page["decisions_next_id"] == "H-0001"
    assert [h["id"] for h in client.get(T + "/history", headers=curator).json()["history"]] == ["DEC-0001"]
    r = client.get(T + "/pending", headers=curator).json()
    assert r["decisions"] == [] and r["applied"] == []           # repo decisions are not hosted ones
    assert r["errors"] == {} and r["ok"] is True
    assert r["repository"]["url"] is None and r["repository"]["workflow_url"] is None   # a dev: resident
    assert r["repository"]["commit"] == pd.commit
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
    r = client.post(T + "/decisions", json=decision_body(toy_hash), headers=curator)
    assert r.status_code == 201
    hist = client.get(T + "/history", headers=curator).json()["history"]
    assert [h["id"] for h in hist] == ["H-0001"] and hist[0]["plugin_errors"] == []
    pend = client.get(T + "/pending", headers=curator).json()
    assert [d["id"] for d in pend["decisions"]] == ["H-0001"] and pend["decisions"][0]["effects"] is None
    subj = client.get(T + "/subject?sid=term:TOY_0001", headers=curator).json()
    assert [d["id"] for d in subj["decisions"]] == ["H-0001"]


def test_landed_decisions_are_marked_applied(client, engine, curator, toy_hash, toy_root):
    """`cwb pull` lands H-0001 in the repo as DEC-0001; the next sync marks it
    applied, the Approved page moves it to `applied`, and export stops sending it."""
    for sid in ("term:TOY_0001", "term:TOY_0002"):
        assert client.post(T + "/decisions", json=decision_body(toy_hash, sid=sid),
                           headers=curator).status_code == 201
    exp = client.get(T + "/decisions/export", headers=curator).json()
    assert [d["id"] for d in exp["decisions"]] == ["H-0001", "H-0002"] and exp["held"] == []
    wb = Workbench.open(toy_root / "workbench.yaml")
    landing.append_hosted(wb.cfg.decisions, wb.cfg.decisions, exp["decisions"][:1])
    YamlStore(wb.cfg.decisions).mark_applied({"DEC-0001": {
        "on": "2026-10-22", "subject_hashes_after": {}, "created": [], "retired": []}})
    publish(engine, "toy", toy_root)
    with engine.connect() as conn:
        commit = project_data.load(conn, "toy").commit
    rec = client.get(T + "/decisions/H-0001", headers=curator).json()["decision"]
    assert rec["status"] == "applied"
    assert rec["applied"]["repo_id"] == "DEC-0001" and rec["applied"]["commit"] == commit
    pend = client.get(T + "/pending", headers=curator).json()
    assert [d["id"] for d in pend["decisions"]] == ["H-0002"]
    assert [d["id"] for d in pend["applied"]] == ["H-0001"]
    assert [d["id"] for d in client.get(T + "/decisions/export", headers=curator).json()["decisions"]] == ["H-0002"]
    hist = [h["id"] for h in client.get(T + "/history", headers=curator).json()["history"]]
    assert "H-0001" not in hist and {"DEC-0001", "H-0002"} <= set(hist)
    assert client.get("/api/v1/projects/toy", headers=curator).json()["decision_counts"]["applied"] == 1


def test_export_holds_back_stale_decisions(client, engine, curator, toy_hash):
    from sqlalchemy import select

    from curation_workbench.store.pg import PgStore
    from curation_workbench.store.pg.tables import decisions
    assert client.post(T + "/decisions", json=decision_body(toy_hash), headers=curator).status_code == 201
    with engine.connect() as conn:
        pd = project_data.load(conn, "toy")
    st = PgStore(engine, pd.project["id"])
    with engine.connect() as conn:
        row_id = conn.execute(select(decisions.c.id).where(decisions.c.dec_id == "H-0001")).scalar_one()
    st.set_stale("decision", row_id, pd.snapshot_id, "subject_changed")
    exp = client.get(T + "/decisions/export", headers=curator).json()
    assert exp["decisions"] == []
    assert exp["held"] == [{"id": "H-0001", "reason": "the item changed since it was decided"}]
