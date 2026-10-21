"""Votes and question counts."""
import pytest
from .hostedtools import vote_body

from curation_workbench.hosted.service import projects as project_svc
from curation_workbench.hosted.service import roles
from curation_workbench.store.pg import PgStore

pytestmark = pytest.mark.postgres

T = "/api/v1/projects/toy"
SID = "term:TOY_0001"
LINKS = "vocabulary/links"


def questions(client, h, **q):
    r = client.get(T + "/questions", params=q, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["questions"]


def test_upsert_retract_and_counts(client, team, auth_headers, toy_hash):
    hv, hv2, hc = (auth_headers(u) for u in (team.voter, team.voter2, team.curator))
    r = client.put(T + "/votes", json=vote_body(toy_hash, comment="ok by me"), headers=hv)
    assert r.status_code == 200, r.text
    v = r.json()["vote"]
    assert v["sid"] == SID and v["review_type"] == LINKS and v["answer"] == "keep"
    assert v["user"] == {"id": team.voter.id, "name": "Vic Voter"} and v["rev"] == 1
    assert v["former_member"] is False and v["stale"] is False and v["comment"] == "ok by me"
    r = client.put(T + "/votes", json=vote_body(toy_hash, answer="delete", verdict="delete",
                                                 proposal={"verdict": "delete",
                                                           "rationale": "unused"}), headers=hv)
    assert r.status_code == 200 and r.json()["vote"]["id"] == v["id"] and r.json()["vote"]["rev"] == 2
    client.put(T + "/votes", json=vote_body(toy_hash), headers=hv2)
    client.put(T + "/votes", json=vote_body(toy_hash), headers=hc)
    [q] = questions(client, hv)
    assert q["state"] == "open" and q["total"] == 3 and q["stale"] == 0
    assert [(a["answer"], a["count"]) for a in q["answers"]] == [("keep", 2), ("delete", 1)]
    assert q["answers"][1]["proposal"] == {"verdict": "delete", "rationale": "unused"}
    assert q["mine"]["answer"] == "delete" and q["mine"]["vote_id"] == v["id"]
    assert questions(client, hv, review_type="subject") == []
    assert questions(client, hv, state="resolved") == []
    assert len(questions(client, hv, sid=SID, state="open")) == 1
    votes = client.get(T + "/votes", params={"sid": SID, "review_type": LINKS}, headers=hv).json()["votes"]
    assert [x["user"]["name"] for x in votes] == ["Vic Voter", "Val Voter", "Cora Curator"]
    subj = client.get(T + "/subject", params={"sid": SID}, headers=hv).json()
    assert len(subj["votes"]) == 3
    assert client.get(T, headers=hv).json()["open_questions"] == 1

    r = client.delete(T + "/votes", params={"sid": SID, "review_type": LINKS}, headers=hv)
    assert r.status_code == 204 and r.content == b""
    r = client.delete(T + "/votes", params={"sid": SID, "review_type": LINKS}, headers=hv)
    assert r.status_code == 404
    [q] = questions(client, hv)
    assert q["total"] == 2 and q["mine"] is None


def test_stale_votes_are_not_counted(client, engine, team, auth_headers, toy_hash):
    hv, hv2 = auth_headers(team.voter), auth_headers(team.voter2)
    a = client.put(T + "/votes", json=vote_body(toy_hash), headers=hv).json()["vote"]
    client.put(T + "/votes", json=vote_body(toy_hash), headers=hv2)
    st = PgStore(engine, _pid(engine))
    with engine.begin() as conn:
        snap = conn.exec_driver_sql("SELECT current_snapshot_id FROM cw_residents").scalar_one()
    st.set_stale("vote", a["id"], snap, "subject_changed")
    [q] = questions(client, hv)
    assert q["total"] == 1 and q["stale"] == 1
    assert q["answers"] == [{"answer": "keep", "count": 1, "stale": 1,
                             "vote_ids": [q["answers"][0]["vote_ids"][0]],
                             "proposal": {"verdict": "keep", "rationale": "looks right", "evidence": []}}]
    assert q["mine"]["stale"] is True
    client.put(T + "/votes", json=vote_body(toy_hash), headers=hv)     # voting again clears it
    [q] = questions(client, hv)
    assert q["total"] == 2 and q["stale"] == 0


def _pid(engine):
    with engine.connect() as conn:
        return conn.exec_driver_sql("SELECT id FROM projects WHERE slug = 'toy'").scalar_one()


def test_stale_hash_and_bad_votes(client, team, auth_headers, toy_hash):
    hv = auth_headers(team.voter)
    r = client.put(T + "/votes", json=vote_body(toy_hash, subject_hash="0" * 64), headers=hv)
    assert r.status_code == 409 and "changed since it was read" in r.json()["detail"]
    r = client.put(T + "/votes", json=vote_body(toy_hash, proposal={"verdict": "explode"}), headers=hv)
    assert r.status_code == 400
    assert r.json()["code"] == "invalid" and "'explode' is not allowed" in r.json()["detail"]
    r = client.put(T + "/votes", json=vote_body(toy_hash, review_type="nope/nope"), headers=hv)
    assert r.status_code == 400 and "unknown review type" in r.json()["detail"]
    r = client.put(T + "/votes", json=vote_body(toy_hash, answer="Bad Answer!"), headers=hv)
    assert r.status_code == 400
    r = client.put(T + "/votes", json=vote_body(toy_hash, review_type="subject", answer="delete"),
                   headers=hv)
    assert r.status_code == 400 and "proposal whose verdict is the answer" in r.json()["detail"]
    r = client.put(T + "/votes", json=vote_body(toy_hash, review_type="subject"), headers=hv)
    assert r.status_code == 200


def test_bad_proposal_same_text_as_the_validator(client, engine, team, auth_headers, toy_hash):
    from curation_workbench import store
    from curation_workbench.hosted import project_data
    body = vote_body(toy_hash, proposal={"verdict": "rename", "operation": {"bogus": 1}})
    r = client.put(T + "/votes", json=body, headers=auth_headers(team.voter))
    assert r.status_code == 400
    with engine.connect() as conn:
        pd = project_data.load(conn, "toy")
    vote = {("subject" if k == "sid" else k): v for k, v in body.items()}
    with pytest.raises(store.StoreError) as e:
        store.validate_vote(vote, project_data.context(pd))
    assert r.json()["errors"] == e.value.errors


def test_removed_member_votes_are_excluded(client, engine, team, auth_headers, toy_hash):
    hv, hv2 = auth_headers(team.voter), auth_headers(team.voter2)
    client.put(T + "/votes", json=vote_body(toy_hash), headers=hv)
    client.put(T + "/votes", json=vote_body(toy_hash), headers=hv2)
    project_svc.remove_member(engine, roles.OPERATOR, "toy", team.voter2.id)
    [q] = questions(client, hv)
    assert q["total"] == 1 and q["answers"][0]["count"] == 1
    votes = client.get(T + "/votes", params={"sid": SID, "review_type": LINKS}, headers=hv).json()["votes"]
    assert [v["former_member"] for v in votes] == [False, True]
    r = client.put(T + "/votes", json=vote_body(toy_hash), headers=hv2)
    assert r.status_code == 404


def test_votes_on_a_resolved_question_are_refused(client, team, auth_headers, toy_hash):
    hv, hc = auth_headers(team.voter), auth_headers(team.curator)
    client.put(T + "/votes", json=vote_body(toy_hash), headers=hv)
    r = client.post(T + "/questions/resolve", json={"sid": SID, "review_type": LINKS,
                                                    "outcome": "no_action"}, headers=hc)
    assert r.status_code == 200, r.text
    r = client.put(T + "/votes", json=vote_body(toy_hash), headers=hv)
    assert r.status_code == 409 and r.json()["code"] == "question_resolved"
    r = client.delete(T + "/votes", params={"sid": SID, "review_type": LINKS}, headers=hv)
    assert r.status_code == 409 and r.json()["code"] == "question_resolved"


def test_writes_request_a_derive(client, engine, team, auth_headers, toy_hash):
    with engine.connect() as conn:
        n0 = conn.exec_driver_sql("SELECT count(*) FROM cw_sync_jobs WHERE kind = 'derive' "
                                  "AND status = 'queued'").scalar_one()
    client.put(T + "/votes", json=vote_body(toy_hash), headers=auth_headers(team.voter))
    with engine.connect() as conn:
        n1 = conn.exec_driver_sql("SELECT count(*) FROM cw_sync_jobs WHERE kind = 'derive' "
                                  "AND status = 'queued'").scalar_one()
    assert (n0, n1) == (0, 1)
