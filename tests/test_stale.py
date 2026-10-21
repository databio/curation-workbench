"""Staleness rules through publish_snapshot: a changed hash marks
`subject_changed`, a rename marks `renamed` with the new SID, a removal marks
`subject_removed`, a revert clears; nothing is deleted, every change is
logged, and applied or repo decisions are never touched."""
import copy
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from curation_workbench import snapshot, store
from curation_workbench.hosted.sync import publish, residents, stale
from curation_workbench.hosted.sync.tables import cw_stale_events
from curation_workbench.store.pg import PgStore, projects
from curation_workbench.workbench import Workbench

pytestmark = pytest.mark.postgres
TOY = Path(__file__).parent / "fixtures" / "toy"
REVIEW_TYPES = {"version": 1, "tracks": [
    {"id": "vocabulary", "kinds": ["term", "enum"], "types": [{"id": "links"}]},
    {"id": "schema", "kinds": ["class", "slot"], "types": [{"id": "fields"}]}]}


@pytest.fixture
def env(pg_engine, tmp_path):
    root = tmp_path / "toy"
    shutil.copytree(TOY, root)
    bundle = snapshot.build(Workbench.open(root / "workbench.yaml"))
    pid = projects.create_project(pg_engine, "toy", "Toy")["id"]
    with pg_engine.begin() as conn:
        rid = residents.ensure_dev_resident(conn, pid, str(root))
    st = PgStore(pg_engine, pid)
    ctx = store.ValidationContext(subjects=store.Subjects(bundle["subjects"]),
                                  verdicts=bundle["verdicts"], review_types=REVIEW_TYPES)
    return pg_engine, rid, st, ctx, bundle


def publish_(engine, rid, bundle, sha):
    with engine.begin() as conn:
        return publish.publish_snapshot(conn, rid, bundle, sha)


def h(bundle, sid):
    return bundle["subjects"]["subjects"][sid]["hash"]


def vote(st, ctx, bundle, sid, user=1):
    return st.cast_vote({"subject": sid, "review_type": "vocabulary/links", "answer": "keep",
                         "subject_hash": h(bundle, sid)}, ctx, user_id=user)


def decide(st, ctx, bundle, sid):
    return st.create({"subject": sid, "verdict": "keep", "rationale": "ok", "decided_by": "a",
                      "subject_hashes": {sid: h(bundle, sid)}}, ctx)


def events(engine):
    with engine.connect() as conn:
        return [(r.object_kind, r.action, r.reason, r.new_sid)
                for r in conn.execute(select(cw_stale_events).order_by(cw_stale_events.c.id))]


def test_judge():
    subjects = {"a": {"hash": "1"}, "b": {"hash": "2"}, "gone": {"hash": "3", "retired": True}}
    aliases = {"old": "b"}
    assert stale.judge("a", "1", subjects, aliases) is None
    assert stale.judge("a", "x", subjects, aliases) == ("subject_changed", None)
    assert stale.judge("old", "2", subjects, aliases) == ("renamed", "b")
    assert stale.judge("gone", "3", subjects, aliases) == ("subject_removed", None)
    assert stale.judge("nope", "3", subjects, aliases) == ("subject_removed", None)


def test_change_rename_remove_revert(env):
    engine, rid, st, ctx, b0 = env
    s0 = publish_(engine, rid, b0, "c0")
    red, blue = "term:TOY_0001", "term:TOY_0002"
    v = vote(st, ctx, b0, red)
    d = decide(st, ctx, b0, blue)
    w = vote(st, ctx, b0, "term:TOY_0002", user=2)

    b1 = copy.deepcopy(b0)
    b1["subjects"]["subjects"][red]["hash"] = "changed"
    s1 = publish_(engine, rid, b1, "c1")
    marks = {x["id"]: x for x in st.open_votes()}
    assert (marks[v["id"]]["stale_reason"], marks[v["id"]]["stale_since_snapshot_id"]) == \
        ("subject_changed", s1)
    assert marks[w["id"]]["stale"] is False

    b2 = copy.deepcopy(b0)
    b2["aliases"] = {blue: "term:TOY_0003"}
    b2["subjects"]["subjects"]["term:TOY_0003"] = b2["subjects"]["subjects"].pop(blue)
    publish_(engine, rid, b2, "c2")
    assert st.get(d["id"])["stale_reason"] == "renamed"
    assert st.get(d["id"])["stale_new_sid"] == "term:TOY_0003"
    assert {x["id"]: x["stale"] for x in st.open_votes()} == {v["id"]: False, w["id"]: True}

    b3 = copy.deepcopy(b0)
    b3["subjects"]["subjects"][blue]["retired"] = True
    publish_(engine, rid, b3, "c3")
    assert st.get(d["id"])["stale_reason"] == "subject_removed"

    publish_(engine, rid, b0, "c0")                       # revert: same commit, reused row
    assert not any(x["stale"] for x in st.open_votes()) and st.get(d["id"])["stale"] is False
    assert len(st.votes()) == 2 and len(st.records()) == 1  # nothing deleted
    ev = events(engine)
    assert ev[0] == ("vote", "marked", "subject_changed", None)
    assert ("decision", "marked", "renamed", "term:TOY_0003") in ev
    assert ev.count(("vote", "cleared", "subject_changed", None)) == 1
    assert [e[1] for e in ev].count("cleared") == 3
    assert s0 != s1


def test_applied_withdrawn_and_repo_decisions_untouched(env):
    engine, rid, st, ctx, b0 = env
    publish_(engine, rid, b0, "c0")
    red = "term:TOY_0001"
    a = decide(st, ctx, b0, red)
    st.mark_applied({a["id"]: {"on": "2026-10-21", "subject_hashes_after": {}, "created": [],
                              "retired": []}})
    wd = decide(st, ctx, b0, red)
    st.withdraw(wd["id"])
    b1 = copy.deepcopy(b0)
    b1["subjects"]["subjects"][red]["hash"] = "changed"
    b1["repo_decisions"] = [{"id": "DEC-0001", "subject": red, "verdict": "keep", "status": "pending",
                             "subject_hashes": {red: h(b0, red)}, "evidence": [],
                             "origin": {"kind": "local", "ref": None}}]
    publish_(engine, rid, b1, "c1")
    assert st.get(a["id"])["stale"] is False and st.get(wd["id"])["stale"] is False
    assert events(engine) == []


def test_revote_clears_mark(env):
    engine, rid, st, ctx, b0 = env
    red = "term:TOY_0001"
    v = vote(st, ctx, b0, red)
    b1 = copy.deepcopy(b0)
    b1["subjects"]["subjects"][red]["hash"] = "changed"
    publish_(engine, rid, b1, "c1")
    assert st.votes()[0]["stale"] is True
    ctx1 = store.ValidationContext(subjects=store.Subjects(b1["subjects"]), verdicts=b1["verdicts"],
                                   review_types=REVIEW_TYPES)
    v2 = st.cast_vote({"subject": red, "review_type": "vocabulary/links", "answer": "keep",
                       "subject_hash": "changed"}, ctx1, user_id=1)
    assert v2["id"] == v["id"] and v2["stale"] is False
