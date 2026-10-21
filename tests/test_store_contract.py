"""The decision store's behavior (Postgres, where decisions are made), and the
trip from there into a repo's decisions.yaml (where they land and are stamped
applied).

`postgres` uses a fresh schema (pg_engine) and skips without
CWB_TEST_POSTGRES_URL. The toy project is copied to tmp_path, so nothing in
the repo changes."""
import shutil
from pathlib import Path

import pytest

from curation_workbench import store, subjects
from curation_workbench.store import landing
from curation_workbench.workbench import Workbench

TOY = Path(__file__).parent / "fixtures" / "toy"
PREFIX = {"postgres": "H-"}


@pytest.fixture
def toy(tmp_path):
    root = tmp_path / "toy"
    shutil.copytree(TOY, root)
    wb = Workbench.open(root / "workbench.yaml")
    files, _ = subjects.render(wb)
    for p, text in files.items():
        p.write_text(text)
    return wb


def toy_ctx(wb):
    subj = store.Subjects.load(wb.cfg.output("subjects.json"))
    return store.ValidationContext(subjects=subj, verdicts=store.load_verdicts(wb.cfg.verdicts), wb=wb)


def make_store(kind, wb, request, slug="toy"):
    from curation_workbench.store.pg import PgStore, projects
    engine = request.getfixturevalue("pg_engine")
    return PgStore(engine, projects.create_project(engine, slug, "Toy")["id"])


@pytest.fixture(params=[pytest.param("postgres", marks=pytest.mark.postgres)])
def backend(request, toy):
    return request.param, make_store(request.param, toy, request), toy_ctx(toy)


def draft(ctx, sid="term:TOY_0001", verdict="keep", **kw):
    rec = {"subject": sid, "verdict": verdict, "rationale": "fine", "decided_by": "tester",
           "decided_on": "2026-10-21", "subject_hashes": {sid: ctx.subjects.get(sid)["hash"]},
           "evidence": [{"kind": "finding", "id": "lint:x"}],
           "schema_fingerprint": ctx.subjects.fingerprint}
    rec.update(kw)
    return rec


def test_create_mints_sequential_ids(backend):
    kind, st, ctx = backend
    a = st.create(draft(ctx), ctx)
    b = st.create(draft(ctx, "term:TOY_0002"), ctx)
    assert (a["id"], b["id"]) == (f"{PREFIX[kind]}0001", f"{PREFIX[kind]}0002")
    assert [r["id"] for r in st.records()] == [a["id"], b["id"]]
    assert st.get(a["id"])["subject"] == "term:TOY_0001"
    if kind == "postgres":
        assert a["origin"] == {"kind": "hosted", "ref": "H-0001"}


def test_update_keeps_id(backend):
    _, st, ctx = backend
    a = st.create(draft(ctx), ctx)
    out = st.update(a["id"], {**draft(ctx), "rationale": "changed"}, ctx)
    assert out["id"] == a["id"]
    assert st.get(a["id"])["rationale"] == "changed"
    assert len(st.records()) == 1


def test_only_pending_can_change(backend):
    _, st, ctx = backend
    a = st.create(draft(ctx), ctx)
    st.withdraw(a["id"])
    with pytest.raises(store.StoreError) as e:
        st.update(a["id"], draft(ctx), ctx)
    assert e.value.status == 400
    assert e.value.errors == [f"{a['id']} is withdrawn; only pending records can change"]


def test_withdraw_refuses_applied(backend):
    _, st, ctx = backend
    a = st.create(draft(ctx), ctx)
    st.mark_applied({a["id"]: {"on": "2026-10-22", "subject_hashes_after": {}, "created": [],
                               "retired": []}})
    with pytest.raises(store.StoreError) as e:
        st.withdraw(a["id"])
    assert e.value.status == 400
    assert "is applied" in e.value.errors[0]


def test_supersedes(backend):
    _, st, ctx = backend
    with pytest.raises(store.StoreError) as e:
        st.create(draft(ctx, supersedes="DEC-0042"), ctx)
    assert e.value.status == 400
    assert e.value.errors == ["supersedes: no earlier decision 'DEC-0042'"]
    a = st.create(draft(ctx), ctx)
    b = st.create(draft(ctx, supersedes=a["id"]), ctx)
    assert b["supersedes"] == a["id"]


def test_changed_hash_conflicts(backend):
    _, st, ctx = backend
    with pytest.raises(store.StoreError) as e:
        st.create(draft(ctx, subject_hashes={"term:TOY_0001": "0" * 64}), ctx)
    assert e.value.status == 409
    assert st.records() == []


def test_stale_token_conflicts(backend):
    _, st, ctx = backend
    a = st.create(draft(ctx), ctx)
    with pytest.raises(store.StoreError) as e:
        st.create(draft(ctx), ctx, expect_token="12345")
    assert e.value.status == 409
    with pytest.raises(store.StoreError) as e:
        st.withdraw(a["id"], expect_token="12345")
    assert e.value.status == 409
    st.withdraw(a["id"], expect_token=st.token())
    assert st.get(a["id"])["status"] == "withdrawn"


def test_mark_applied(backend):
    _, st, ctx = backend
    a = st.create(draft(ctx), ctx)
    block = {"on": "2026-10-22", "subject_hashes_after": {"term:TOY_0001": "abc"},
             "created": [], "retired": [], "renamed": {"term:X": "term:Y"}}
    st.mark_applied({a["id"]: block})
    rec = st.get(a["id"])
    assert rec["status"] == "applied"
    assert rec["applied"] == block
    with pytest.raises(store.StoreError) as e:
        st.mark_applied({"DEC-0999": block})
    assert e.value.status == 404


def test_yaml_store_has_no_write_path(toy):
    """decisions.yaml is a record, not a place decisions are made."""
    assert not any(hasattr(toy.store, m) for m in ("create", "update", "withdraw"))


def test_yaml_mark_applied(toy):
    ctx = toy_ctx(toy)
    landing.append_hosted(toy.cfg.decisions, toy.cfg.decisions,
                          [store.normalize({**draft(ctx), "id": "H-0001", "status": "pending"})])
    block = {"on": "2026-10-22", "subject_hashes_after": {"term:TOY_0001": "abc"},
             "created": [], "retired": [], "renamed": {"term:X": "term:Y"}}
    toy.store.mark_applied({"DEC-0001": block})
    rec = toy.store.get("DEC-0001")
    assert rec["status"] == "applied" and rec["applied"] == block
    assert rec["origin"] == {"kind": "hosted", "ref": "H-0001"}
    with pytest.raises(store.StoreError) as e:
        toy.store.mark_applied({"DEC-0999": block})
    assert e.value.status == 404


@pytest.mark.postgres
def test_records_survive_the_trip_to_decisions_yaml(toy, request, tmp_path):
    """Every field a hosted decision has comes out the same in decisions.yaml
    (ids renumbered, origin pointing back), key order too."""
    from curation_workbench.store.pg import export_yaml
    ctx = toy_ctx(toy)
    pg = make_store("postgres", toy, request)
    first = pg.create(draft(ctx), ctx)
    pg.mark_applied({first["id"]: {"on": "2026-10-22", "subject_hashes_after": {}, "created": [],
                                   "retired": []}})
    landing_template = tmp_path / "landed.yaml"
    export_yaml(pg, toy.cfg.decisions, landing_template, ids=[])
    # H-0001 already landed in the repo (as DEC-0001); H-0002 supersedes it.
    landing.append_hosted(toy.cfg.decisions, landing_template, [pg.get(first["id"])])
    pg.create(draft(ctx, "term:TOY_0002", verdict="edit_description",
                    operation={"op": "edit_description", "description": "Blue."},
                    also_affects=["term:TOY_0001"], supersedes=first["id"],
                    subject_hashes={s: ctx.subjects.get(s)["hash"] for s in ("term:TOY_0001", "term:TOY_0002")}),
              ctx)
    out = tmp_path / "decisions.yaml"
    assert export_yaml(pg, landing_template, out) == {"H-0002": "DEC-0002"}
    p = {k: v for k, v in pg.get("H-0002").items()
         if k not in ("rev", "vote_ids", "stale", "stale_reason", "stale_new_sid")}
    y = store.YamlStore(out).records()[-1]
    assert (y["id"], y["supersedes"], y["origin"]) == ("DEC-0002", "DEC-0001", {"kind": "hosted", "ref": "H-0002"})
    same = [k for k in p if k not in ("id", "supersedes", "origin")]
    assert [k for k in y if k not in ("id", "supersedes", "origin")] == same      # key order too
    assert {k: y[k] for k in same} == {k: p[k] for k in same}
