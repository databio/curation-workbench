"""A curator turns a question into a decision (or closes it with no action)."""
import pytest
from .hostedtools import decision_body, vote_body
from sqlalchemy import select

from curation_workbench.hosted.tables import question_resolutions
from curation_workbench.store.pg.tables import decisions

pytestmark = pytest.mark.postgres

T = "/api/v1/projects/toy"
SID = "term:TOY_0001"
LINKS = "vocabulary/links"


@pytest.fixture
def voted(client, team, auth_headers, toy_hash):
    """Two keep votes and one delete vote on (TOY_0001, vocabulary/links)."""
    ids = {}
    for user, answer in ((team.voter, "keep"), (team.voter2, "keep"), (team.curator, "delete")):
        body = vote_body(toy_hash, answer=answer, verdict=answer,
                         proposal={"verdict": answer, "rationale": f"{answer} it"})
        ids[user.id] = client.put(T + "/votes", json=body, headers=auth_headers(user)).json()["vote"]["id"]
    return ids


def resolve(client, h, **body):
    return client.post(T + "/questions/resolve", json={"sid": SID, "review_type": LINKS, **body},
                       headers=h)


def test_resolve_from_a_vote(client, engine, team, auth_headers, voted):
    hc = auth_headers(team.curator)
    r = resolve(client, hc, outcome="decision", from_vote=voted[team.voter.id], note="clear")
    assert r.status_code == 200, r.text
    d, q = r.json()["decision"], r.json()["question"]
    assert d["id"] == "H-0001" and d["origin"] == {"kind": "hosted", "ref": "H-0001"}
    assert d["verdict"] == "keep" and d["rationale"] == "keep it"
    assert d["decided_by"] == "Cora Curator <cur@example.org>"
    assert sorted(d["vote_ids"]) == sorted(voted.values())
    assert d["evidence"][-1] == {"kind": "other", "detail": "votes: keep 2, delete 1"}
    assert q["state"] == "resolved" and q["outcome"] == "decision" and q["decision_id"] == "H-0001"
    assert client.get(T + "/decisions/H-0001", headers=hc).json()["decision"]["vote_ids"] == d["vote_ids"]
    r = resolve(client, hc, outcome="no_action")
    assert r.status_code == 409 and r.json()["code"] == "question_resolved"


def test_resolve_from_a_full_record_with_stale_votes(client, engine, team, auth_headers, voted, toy_hash):
    from curation_workbench.store.pg import PgStore
    with engine.connect() as conn:
        pid = conn.exec_driver_sql("SELECT id FROM projects").scalar_one()
        snap = conn.exec_driver_sql("SELECT current_snapshot_id FROM cw_repos").scalar_one()
    PgStore(engine, pid).set_stale("vote", voted[team.voter2.id], snap, "subject_changed")
    rec = decision_body(toy_hash, rationale="curator's own words")
    r = resolve(client, auth_headers(team.curator), outcome="decision", decision=rec)
    assert r.status_code == 200, r.text
    d = r.json()["decision"]
    assert d["rationale"] == "curator's own words"
    assert d["evidence"] == [{"kind": "other", "detail": "x"},
                             {"kind": "other", "detail": "votes: delete 1, keep 1 (1 stale not counted)"}]
    assert sorted(d["vote_ids"]) == sorted([voted[team.voter.id], voted[team.curator.id]])


def test_failed_validation_leaves_the_question_open(client, engine, team, auth_headers, voted, toy_hash):
    hc = auth_headers(team.curator)
    bad = decision_body(toy_hash, verdict="nonsense")
    r = resolve(client, hc, outcome="decision", decision=bad)
    assert r.status_code == 400
    r = resolve(client, hc, outcome="decision", decision=decision_body(toy_hash, "term:TOY_0002"))
    assert r.status_code == 400 and "not the question's subject" in r.json()["detail"]
    with engine.connect() as conn:
        assert conn.execute(select(decisions.c.id)).all() == []
        assert conn.execute(select(question_resolutions.c.id)).all() == []
    [q] = client.get(T + "/questions", headers=hc).json()["questions"]
    assert q["state"] == "open"
    r = resolve(client, hc, outcome="decision", from_vote=999999)
    assert r.status_code == 400
    r = resolve(client, hc, outcome="decision")
    assert r.status_code == 400
    r = resolve(client, hc, outcome="maybe")
    assert r.status_code == 400


def test_no_action_and_reopen(client, team, auth_headers, voted):
    hc = auth_headers(team.curator)
    r = resolve(client, hc, outcome="no_action", note="not worth it")
    assert r.status_code == 200 and r.json()["decision"] is None
    assert r.json()["question"]["outcome"] == "no_action"
    r = client.post(T + "/questions/reopen", json={"sid": SID, "review_type": LINKS}, headers=hc)
    assert r.status_code == 200 and r.json()["question"]["state"] == "open"
    r = client.post(T + "/questions/reopen", json={"sid": SID, "review_type": LINKS}, headers=hc)
    assert r.status_code == 409


def test_reopen_only_after_withdraw(client, team, auth_headers, voted, toy_hash):
    hc = auth_headers(team.curator)
    resolve(client, hc, outcome="decision", from_vote=voted[team.voter.id])
    body = {"sid": SID, "review_type": LINKS}
    r = client.post(T + "/questions/reopen", json=body, headers=hc)
    assert r.status_code == 409 and r.json()["code"] == "decision_still_active"
    r = client.delete(T + "/decisions/H-0001", headers={**hc, "If-Match": "1"})
    assert r.status_code == 200
    r = client.post(T + "/questions/reopen", json=body, headers=hc)
    assert r.status_code == 200 and r.json()["question"]["state"] == "open"
    r = client.put(T + "/votes", json=vote_body(toy_hash), headers=auth_headers(team.voter))
    assert r.status_code == 200      # open again: votes are accepted


def test_voter_cannot_resolve(client, team, auth_headers, voted):
    r = resolve(client, auth_headers(team.voter), outcome="no_action")
    assert r.status_code == 403 and r.json()["code"] == "not_curator"
