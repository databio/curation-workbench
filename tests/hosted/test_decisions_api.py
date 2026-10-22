"""Hosted decisions: ids, server-owned fields, If-Match, conflicts, validation, export."""
import pytest
from .hostedtools import decision_body, publish

from curation_workbench import store
from curation_workbench.hosted import project_data
from curation_workbench.hosted.service import decisions as svc

pytestmark = pytest.mark.postgres

T = "/api/v1/projects/toy/decisions"


@pytest.fixture
def h(team, auth_headers):
    return auth_headers(team.curator)


def test_create_update_withdraw(client, h, toy_hash, team):
    body = decision_body(toy_hash)
    body.update(id="DEC-9999", decided_by="mallory", status="applied", origin={"kind": "local"},
                schema_fingerprint="bogus", decided_on="1999-01-01", applied={"on": "x"}, rev=7,
                vote_ids=[1], stale=True, decided_by_user_id=99)
    r = client.post(T, json=body, headers=h)
    assert r.status_code == 201, r.text
    d = r.json()["decision"]
    assert d["id"] == "H-0001" and d["origin"] == {"kind": "hosted", "ref": "H-0001"}
    assert d["decided_by"] == "Cora Curator <cur@example.org>" and d["status"] == "pending"
    assert d["decided_on"] != "1999-01-01" and d["schema_fingerprint"] != "bogus"
    assert d["rev"] == 1 and d["vote_ids"] == [] and d["stale"] is False
    assert "decided_by_user_id" not in d
    d2 = client.post(T, json=decision_body(toy_hash, "term:TOY_0002"), headers=h).json()["decision"]
    assert d2["id"] == "H-0002"

    r = client.put(f"{T}/H-0001", json=decision_body(toy_hash, rationale="better"), headers=h)
    assert r.status_code == 428 and r.json()["code"] == "precondition_required"
    r = client.put(f"{T}/H-0001", json=decision_body(toy_hash, rationale="better"),
                   headers={**h, "If-Match": "1"})
    assert r.status_code == 200 and r.json()["decision"]["rationale"] == "better"
    assert r.json()["decision"]["rev"] == 2
    r = client.put(f"{T}/H-0001", json=decision_body(toy_hash, rationale="again"),
                   headers={**h, "If-Match": "1"})
    assert r.status_code == 409 and r.json()["code"] == "conflict"
    assert r.json()["current"]["rev"] == 2
    assert client.delete(f"{T}/H-0001", headers=h).status_code == 428
    r = client.delete(f"{T}/H-0001", headers={**h, "If-Match": '"2"'})
    assert r.status_code == 200 and r.json()["decision"]["status"] == "withdrawn"
    assert client.get(f"{T}/H-0001", headers=h).json()["decision"]["status"] == "withdrawn"
    assert client.get(f"{T}/H-0404", headers=h).status_code == 404

    lst = client.get(T, headers=h).json()
    assert lst["next_id"] == "H-0003" and lst["version"] == 1 and lst["token"]
    assert [x["id"] for x in lst["decisions"]] == ["H-0001", "H-0002"]
    assert [x["id"] for x in client.get(T + "?status=pending", headers=h).json()["decisions"]] == ["H-0002"]

    exp = client.get(T + "/export", headers=h).json()["decisions"]
    assert [x["id"] for x in exp] == ["H-0002"]
    assert not {"rev", "vote_ids", "stale", "stale_reason", "stale_new_sid"} & set(exp[0])
    assert exp[0]["decided_on"].count("-") == 2


def test_changed_subject_hash_is_409(client, h, toy_hash):
    body = decision_body(toy_hash)
    body["subject_hashes"] = {"term:TOY_0001": "0" * 64}
    r = client.post(T, json=body, headers=h)
    assert r.status_code == 409 and "changed since it was read" in r.json()["detail"]


def test_validation_errors_match_the_store(client, engine, h, toy_hash):
    with engine.connect() as conn:
        pd = project_data.load(conn, "toy")
    bad = decision_body(toy_hash, verdict="rename", operation={"label": 5})
    r = client.post(T, json=bad, headers=h)
    assert r.status_code == 400 and r.json()["code"] == "invalid"
    rec = svc.server_fields(svc.client_record(bad), type("C", (), {"user": type(
        "U", (), {"name": "Cora Curator", "email": "cur@example.org"})})(), pd)
    rec.update(id="H-0001", origin={"kind": "hosted", "ref": "H-0001"})
    with pytest.raises(store.StoreError) as e:
        store.validate(store.normalize(rec), pd.subjects, pd.bundle["verdicts"], known_ids=set())
    assert r.json()["errors"] == e.value.errors
    assert r.json()["detail"] == e.value.errors[0]


def test_supersede_a_repo_decision(client, engine, h, toy_root, toy_hash):
    from .hostedtools import add_repo_decision
    add_repo_decision(toy_root)
    publish(engine, "toy", toy_root)
    body = decision_body(toy_hash, "term:TOY_0002", supersedes="DEC-0001")
    r = client.post(T, json=body, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["decision"]["supersedes"] == "DEC-0001"
    r = client.post(T, json=decision_body(toy_hash, supersedes="DEC-0042"), headers=h)
    assert r.status_code == 400 and "supersedes" in r.json()["detail"]


def test_decided_by_user_id_column(client, engine, h, team, toy_hash):
    from sqlalchemy import select

    from curation_workbench.store.pg.tables import decisions
    client.post(T, json=decision_body(toy_hash), headers=h)
    with engine.connect() as conn:
        assert conn.execute(select(decisions.c.decided_by_user_id)).scalar_one() == team.curator.id
