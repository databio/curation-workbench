"""Postgres-only behavior of the hosted store: export, concurrency, transactions,
votes, project scoping, the driver and the migration runner.

Needs CWB_TEST_POSTGRES_URL (scripts/test-postgres.sh); skips otherwise. The
ONGA export case reads ONGA_ROOT read-only and writes only to tmp_path."""
import shutil
import threading
from pathlib import Path

import pytest
from conftest import seed_users
from test_store_contract import draft, toy, toy_ctx  # noqa: F401  (toy is a fixture)

sqlalchemy = pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from sqlalchemy import text  # noqa: E402

from curation_workbench import store  # noqa: E402
from curation_workbench.store.pg import PgStore, export_yaml, make_engine, migrate, projects  # noqa: E402
from curation_workbench.workbench import Workbench  # noqa: E402

TOY = Path(__file__).parent / "fixtures" / "toy"
REVIEW_TYPES = {"version": 1, "tracks": [
    {"id": "vocabulary", "kinds": ["term", "enum"], "types": [{"id": "links"}, {"id": "keep-or-cut"}]},
    {"id": "schema", "kinds": ["class", "slot"], "types": [{"id": "fields"}]},
]}


def test_driver_is_psycopg3():
    eng = make_engine("postgresql://u:p@localhost:5432/x")
    assert eng.dialect.driver == "psycopg"
    assert make_engine("postgres://u:p@localhost/x").dialect.driver == "psycopg"
    with pytest.raises(RuntimeError, match="psycopg 3"):
        make_engine("postgresql+psycopg2://u:p@localhost/x")


def test_database_url_from_env():
    from curation_workbench.store.pg import database_url_from_env
    assert database_url_from_env({"CWB_DATABASE_URL": "postgresql+psycopg://a@b/c"}) == \
        "postgresql+psycopg://a@b/c"
    url = database_url_from_env({"POSTGRES_DB": "cwb", "POSTGRES_USER": "u",
                                 "POSTGRES_PASSWORD": "p@ss/word", "POSTGRES_HOST": "h"})
    assert url == "postgresql+psycopg://u:p%40ss%2Fword@h:5432/cwb"
    with pytest.raises(RuntimeError):
        database_url_from_env({})


pytestmark = pytest.mark.postgres


@pytest.fixture
def project(pg_engine):
    seed_users(pg_engine, 1, 2, 3, 7)
    return PgStore(pg_engine, projects.create_project(pg_engine, "toy", "Toy")["id"])


@pytest.fixture
def snap(project):
    """A real cw_snapshots id (040 adds the stale columns' foreign key to it)."""
    with project.engine.begin() as conn:
        rid = conn.execute(text("INSERT INTO cw_residents (project_id, repo_url, branch) "
                                "VALUES (:p, 'dev:/x', 'dev') RETURNING id"),
                           {"p": project.project_id}).scalar_one()
        return conn.execute(text("INSERT INTO cw_snapshots (resident_id, commit_sha, "
                                 "schema_fingerprint, bundle, bundle_sha256) "
                                 "VALUES (:r, 'c', 'f', '\\x00', 'h') RETURNING id"),
                            {"r": rid}).scalar_one()


@pytest.fixture
def ctx(toy):  # noqa: F811
    c = toy_ctx(toy)
    c.review_types = REVIEW_TYPES
    return c


def vote(ctx, sid="term:TOY_0001", review_type="vocabulary/links", answer="keep", **kw):
    v = {"subject": sid, "review_type": review_type, "answer": answer,
         "subject_hash": ctx.subjects.get(sid)["hash"]}
    v.update(kw)
    return v


# ---------------------------------------------------------------- migrations

def test_migrations(pg_engine):
    files = [p.name for p in migrate.migration_files()]
    assert files == ["001_snapgate.sql", "010_projects.sql", "011_decisions.sql", "012_votes.sql",
                     "013_decision_votes.sql", "020_project_members.sql",
                     "021_question_resolutions.sql", "022_user_fks.sql", "040_residents_sync.sql",
                     "090_snapgate_useradmin.sql"]
    with pg_engine.connect() as conn:
        ledger = sorted(r[0] for r in conn.execute(text("SELECT filename FROM schema_migrations")))
    assert ledger == files
    assert migrate.migrate(pg_engine) == []          # a rerun is a no-op
    assert migrate.pending_migrations(pg_engine) == []
    assert migrate.unknown_migrations(pg_engine) == []
    assert migrate.missing_schema(pg_engine) == []
    migrate.assert_schema_current(pg_engine)
    with pg_engine.begin() as conn:
        conn.execute(text("ALTER TABLE votes DROP COLUMN comment"))
        conn.execute(text("INSERT INTO schema_migrations (filename) VALUES ('099_gone.sql')"))
    assert migrate.missing_schema(pg_engine) == ["votes.comment"]
    with pytest.raises(migrate.SchemaDriftError, match="votes.comment.*|099_gone"):
        migrate.assert_schema_current(pg_engine)


def test_projects(pg_engine):
    p = projects.create_project(pg_engine, "onga", "ONGA")
    assert projects.get_project(pg_engine, "onga")["id"] == p["id"]
    with pytest.raises(store.StoreError) as e:
        projects.create_project(pg_engine, "onga", "again")
    assert e.value.status == 409
    with pytest.raises(store.StoreError):
        projects.create_project(pg_engine, "Bad Slug", "x")
    assert projects.rename_project(pg_engine, "onga", "ONGA 2")["name"] == "ONGA 2"
    projects.archive_project(pg_engine, "onga")
    assert projects.list_projects(pg_engine) == []
    assert [x["slug"] for x in projects.list_projects(pg_engine, include_archived=True)] == ["onga"]


# ---------------------------------------------------------------- scoping, tokens

def test_project_scoping(pg_engine, ctx):
    seed_users(pg_engine, 1)
    a = PgStore(pg_engine, projects.create_project(pg_engine, "aa", "A")["id"])
    b = PgStore(pg_engine, projects.create_project(pg_engine, "bb", "B")["id"])
    ra = a.create(draft(ctx), ctx)
    rb = b.create(draft(ctx, "term:TOY_0002"), ctx)
    assert ra["id"] == rb["id"] == "H-0001"
    assert [r["subject"] for r in a.records()] == ["term:TOY_0001"]
    assert [r["subject"] for r in b.records()] == ["term:TOY_0002"]
    a.withdraw("H-0001")
    assert b.get("H-0001")["status"] == "pending"
    va = a.cast_vote(vote(ctx), ctx, user_id=1)
    assert b.votes() == []
    with pytest.raises(store.StoreError) as e:   # a vote from another project
        b.create(draft(ctx), ctx, vote_ids=[va["id"]])
    assert e.value.status == 400
    with pytest.raises(store.StoreError) as e:
        b.set_stale("vote", va["id"], 1, "subject_changed")
    assert e.value.status == 404


def test_token_and_snapshot(project, ctx):
    assert project.decisions_rev() == 0
    project.create(draft(ctx), ctx)
    project.cast_vote(vote(ctx), ctx, user_id=7)
    assert project.token() == "2"
    snap = project.snapshot()
    assert snap["version"] == 1 and snap["next_id"] == 2 and snap["token"] == "2"
    assert [r["id"] for r in snap["decisions"]] == ["H-0001"]


def test_expect_rev_conflict_returns_current(project, ctx):
    a = project.create(draft(ctx), ctx)
    project.update(a["id"], {**draft(ctx), "rationale": "v2"}, ctx, expect_rev=1)
    with pytest.raises(store.StoreError) as e:
        project.update(a["id"], {**draft(ctx), "rationale": "v3"}, ctx, expect_rev=1)
    assert e.value.status == 409
    assert e.value.current["rationale"] == "v2" and e.value.current["rev"] == 2


# ---------------------------------------------------------------- concurrency

def race(*fns):
    """Run fns at the same moment in threads; [(result or StoreError)] in order."""
    barrier = threading.Barrier(len(fns))
    out = [None] * len(fns)

    def run(i, fn):
        barrier.wait()
        try:
            out[i] = fn()
        except store.StoreError as e:
            out[i] = e
    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def test_concurrent_update_same_rev(pg_engine, project, ctx):
    a = project.create(draft(ctx), ctx)
    s1, s2 = PgStore(pg_engine, project.project_id), PgStore(pg_engine, project.project_id)
    out = race(lambda: s1.update(a["id"], {**draft(ctx), "rationale": "one"}, ctx, expect_rev=1),
               lambda: s2.update(a["id"], {**draft(ctx), "rationale": "two"}, ctx, expect_rev=1))
    wins = [o for o in out if isinstance(o, dict)]
    losses = [o for o in out if isinstance(o, store.StoreError)]
    assert len(wins) == 1 and len(losses) == 1
    assert losses[0].status == 409
    assert losses[0].current["rationale"] == wins[0]["rationale"]
    assert project.get(a["id"])["rev"] == 2


def test_concurrent_creates(pg_engine, project, ctx):
    s1, s2 = PgStore(pg_engine, project.project_id), PgStore(pg_engine, project.project_id)
    out = race(lambda: s1.create(draft(ctx), ctx), lambda: s2.create(draft(ctx, "term:TOY_0002"), ctx))
    assert sorted(o["id"] for o in out) == ["H-0001", "H-0002"]
    assert project.snapshot()["next_id"] == 3


# ---------------------------------------------------------------- transaction()

def test_transaction_rolls_back(project, ctx):
    with pytest.raises(sqlalchemy.exc.DBAPIError):
        with project.transaction() as conn:
            project.create(draft(ctx), ctx)
            assert len(project.records()) == 1     # visible inside the block
            conn.execute(text("SELECT 1/0"))
    assert project.records() == []
    snap = project.snapshot()
    assert (snap["next_id"], snap["token"]) == (1, "0")


def test_transaction_commits(project, ctx):
    with project.transaction():
        project.create(draft(ctx), ctx)
        project.cast_vote(vote(ctx), ctx, user_id=1)
    assert len(project.records()) == 1 and len(project.votes()) == 1


# ---------------------------------------------------------------- votes

def test_one_vote_per_person(project, ctx, snap):
    v1 = project.cast_vote(vote(ctx), ctx, user_id=1)
    project.set_stale("vote", v1["id"], snap, "subject_changed")
    assert project.votes()[0]["stale"] is True
    v2 = project.cast_vote(vote(ctx, answer="no-match", comment="changed my mind"), ctx, user_id=1)
    assert v2["id"] == v1["id"] and v2["rev"] == 2
    assert v2["answer"] == "no-match" and v2["stale"] is False and v2["stale_reason"] is None
    project.cast_vote(vote(ctx), ctx, user_id=2)
    assert [v["user_id"] for v in project.votes()] == [1, 2]
    assert [v["user_id"] for v in project.votes(user_id=2)] == [2]
    with pytest.raises(store.StoreError) as e:   # compare-and-swap on the vote's rev
        project.cast_vote(vote(ctx), ctx, user_id=1, expect_rev=1)
    assert e.value.status == 409 and e.value.current["rev"] == 2


@pytest.mark.parametrize("bad, status", [
    ({"review_type": "vocabulary/nope"}, 400),
    ({"review_type": "schema/fields"}, 400),                   # a term is not in the schema track
    ({"review_type": "subject", "answer": "keep",
      "proposal": {"verdict": "delete"}}, 400),               # answer != proposal.verdict
    ({"review_type": "subject", "answer": "keep"}, 400),      # no proposal
    ({"answer": "Not A Key"}, 400),
    ({"proposal": {"verdict": "edit_description", "operation": {"op": "edit_description"}}}, 400),
    ({"proposal": {"verdict": "keep", "bogus": 1}}, 400),
    ({"subject_hash": "0" * 64}, 409),
])
def test_vote_validation(project, ctx, bad, status):
    with pytest.raises(store.StoreError) as e:
        project.cast_vote({**vote(ctx), **bad}, ctx, user_id=1)
    assert e.value.status == status
    assert project.votes() == []


def test_subject_vote_with_proposal(project, ctx):
    v = project.cast_vote(vote(ctx, review_type="subject", answer="edit_description",
                               proposal={"verdict": "edit_description",
                                         "operation": {"op": "edit_description", "description": "Red."}}),
                          ctx, user_id=3)
    assert v["proposal"]["operation"]["description"] == "Red."


def test_stale_marks(project, ctx, snap):
    v = project.cast_vote(vote(ctx), ctx, user_id=1)
    d = project.create(draft(ctx), ctx)
    unmarked = {"stale": False, "stale_since_snapshot_id": None, "stale_reason": None,
                "stale_new_sid": None}
    assert project.open_votes() == [{"id": v["id"], "subject": "term:TOY_0001",
                                     "subject_hash": v["subject_hash"], **unmarked}]
    pend = project.pending_decisions()
    assert [p["dec_id"] for p in pend] == ["H-0001"]
    rev = project.decisions_rev()
    project.set_stale("vote", v["id"], snap, "renamed", new_sid="term:TOY_0009")
    project.set_stale("decision", pend[0]["id"], snap, "subject_changed")
    assert project.open_votes()[0]["stale_since_snapshot_id"] == snap
    assert project.decisions_rev() == rev          # marks do not bump the token
    got = project.votes()[0]
    assert (got["stale"], got["stale_reason"], got["stale_new_sid"]) == (True, "renamed", "term:TOY_0009")
    rec = project.get(d["id"])
    assert (rec["stale"], rec["stale_reason"]) == (True, "subject_changed")
    with pytest.raises(store.StoreError):
        project.set_stale("vote", v["id"], snap, "bogus")
    with pytest.raises(store.StoreError):          # 040: the snapshot must exist
        project.set_stale("vote", v["id"], snap + 1000, "renamed")
    project.clear_stale("vote", v["id"])
    project.clear_stale("decision", pend[0]["id"])
    assert project.votes()[0]["stale"] is False and project.get(d["id"])["stale"] is False


def test_linked_votes(project, ctx):
    v1 = project.cast_vote(vote(ctx), ctx, user_id=1)
    v2 = project.cast_vote(vote(ctx, "term:TOY_0002"), ctx, user_id=1)
    with pytest.raises(store.StoreError) as e:      # vote on an unrelated subject
        project.create(draft(ctx), ctx, vote_ids=[v2["id"]])
    assert e.value.status == 400
    with pytest.raises(store.StoreError) as e:
        project.create(draft(ctx), ctx, vote_ids=[999999])
    assert e.value.status == 400
    d = project.create(draft(ctx), ctx, vote_ids=[v1["id"]])
    assert d["vote_ids"] == [v1["id"]]
    assert project.decision_votes(d["id"]) == [{"vote_id": v1["id"], "answer": "keep", "proposal": None,
                                                "subject_hash": v1["subject_hash"], "vote_rev": 1}]
    project.cast_vote(vote(ctx, answer="no-match"), ctx, user_id=1)   # the snapshot keeps "keep"
    assert project.decision_votes(d["id"])[0]["answer"] == "keep"
    with pytest.raises(store.StoreError) as e:
        project.retract_vote("term:TOY_0001", "vocabulary/links", user_id=1)
    assert e.value.status == 409 and e.value.errors == ["this vote was used by H-0001"]
    project.retract_vote("term:TOY_0002", "vocabulary/links", user_id=1)
    with pytest.raises(store.StoreError) as e:
        project.retract_vote("term:TOY_0002", "vocabulary/links", user_id=1)
    assert e.value.status == 404


def test_create_and_update_with_extra_keys(project, ctx):
    d = project.create({**draft(ctx), "rev": 99, "vote_ids": [1], "note": "kept"}, ctx)
    assert d["note"] == "kept" and d["rev"] == 1 and d["vote_ids"] == []
    assert list(d)[:15] == list(store.FIELDS)


# ---------------------------------------------------------------- export

def three_hosted(st, ctx, repo_id):
    """Three hosted decisions: H-0002 supersedes H-0001; H-0003 supersedes a repo id."""
    a = st.create(draft(ctx, ctx.subject_ids[0]), ctx)
    st.create(draft(ctx, ctx.subject_ids[0], supersedes=a["id"]), ctx)
    st.create(draft(ctx, ctx.subject_ids[1], supersedes=repo_id), ctx)


def check_export(st, template, ctx, tmp_path, wb=None):
    out = tmp_path / "out.yaml"
    assert export_yaml(st, template, out) == {}
    assert out.read_bytes() == Path(template).read_bytes()

    repo = store.YamlStore(template).records()
    before = int(store.YamlStore(template).load().data["next_id"])
    repo_id = repo[0]["id"] if repo else None
    ctx.extra_known_ids = frozenset(r["id"] for r in repo)
    if repo_id is None:   # an empty repo log: supersede a hosted one instead
        a = st.create(draft(ctx, ctx.subject_ids[0]), ctx)
        st.create(draft(ctx, ctx.subject_ids[0], supersedes=a["id"]), ctx)
        st.create(draft(ctx, ctx.subject_ids[1]), ctx)
    else:
        three_hosted(st, ctx, repo_id)
    st.withdraw(st.create(draft(ctx, ctx.subject_ids[1]), ctx)["id"])   # not exported

    renumbered = export_yaml(st, template, out)
    ids = [f"DEC-{n:04d}" for n in range(before, before + 3)]
    assert renumbered == dict(zip(["H-0001", "H-0002", "H-0003"], ids))
    got = store.YamlStore(out).records()
    assert got[:len(repo)] == repo
    new = got[len(repo):]
    assert [r["id"] for r in new] == ids
    assert [r["origin"] for r in new] == [{"kind": "hosted", "ref": h} for h in renumbered]
    assert new[1]["supersedes"] == ids[0]
    if repo_id:
        assert new[2]["supersedes"] == repo_id
    assert not any(k in r for r in new for k in ("rev", "vote_ids", "stale"))
    assert int(store.YamlStore(out).load().data["next_id"]) == before + 3
    known = {r["id"] for r in got}
    for r in new:
        r = store.normalize(r)
        store.validate(r, ctx.subjects, ctx.verdicts, known_ids=known, check_hashes=False, wb=wb)


def test_export_toy(project, ctx, toy, tmp_path):  # noqa: F811
    ctx.subject_ids = ["term:TOY_0001", "term:TOY_0002"]
    check_export(project, toy.cfg.decisions, ctx, tmp_path, wb=toy)


def test_export_onga(pg_engine, onga, tmp_path):
    wb = Workbench.open(onga / "workbench.yaml")
    template = tmp_path / "decisions.yaml"
    shutil.copy(onga / "curation" / "decisions.yaml", template)
    subj = store.Subjects.load(onga / "curation" / "subjects.json")
    ctx = store.ValidationContext(subjects=subj, verdicts=store.load_verdicts(wb.cfg.verdicts), wb=wb)
    ctx.subject_ids = [s for s, r in subj.by_sid.items() if r["kind"] == "term" and not r["retired"]][:2]
    st = PgStore(pg_engine, projects.create_project(pg_engine, "onga", "ONGA")["id"])
    check_export(st, template, ctx, tmp_path, wb=wb)
