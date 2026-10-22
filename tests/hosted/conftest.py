"""Fixtures for the hosted API tests (plan 2), modeled on Pigeon's.

Every test here is marked `postgres` and runs on plan 1's `pg_engine` (a
throwaway schema with every migration applied by the real runner; see
tests/conftest.py and scripts/test-postgres.sh). There is no SQLite tier.

Tests never touch real data: the autouse `settings` fixture strips every
`CWB_*` (except `CWB_TEST_POSTGRES_URL`), `POSTGRES_*` and `SNAPGATE_*`
variable (see `isolate_env`).
"""
from __future__ import annotations

import os
import shutil
import uuid

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session  # noqa: E402

from curation_workbench.hosted import project_data  # noqa: E402
from curation_workbench.hosted.app import create_app  # noqa: E402
from curation_workbench.hosted.auth import API_KEY_PREFIX  # noqa: E402
from curation_workbench.hosted.config import get_settings  # noqa: E402
from curation_workbench.hosted.scopes import DEFAULT_SCOPES, SCOPES  # noqa: E402
from curation_workbench.hosted.service import projects as project_svc  # noqa: E402
from curation_workbench.hosted.service import roles  # noqa: E402
from curation_workbench.store.pg import projects as pg_projects  # noqa: E402
from snapgate import User, UserStatus  # noqa: E402
from snapgate import settings as sg_settings  # noqa: E402
from snapgate.api_keys import clear_verified_cache, create_api_key  # noqa: E402
from snapgate.core import create_access_token  # noqa: E402

from .hostedtools import ADMIN_EMAIL, SITE_ADMIN_EMAIL, TOY, publish  # noqa: E402

KEEP = "CWB_TEST_POSTGRES_URL"


def isolate_env(monkeypatch, tmp_path) -> None:
    for key in list(os.environ):
        if (key.startswith("CWB_") and key != KEEP) or key.startswith(("POSTGRES_", "SNAPGATE_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("CWB_ENV", "test")
    monkeypatch.setenv("CWB_FRONTEND_DIST", str(tmp_path / "no-frontend"))
    monkeypatch.setenv("SNAPGATE_SUPERADMIN_EMAILS", ADMIN_EMAIL)
    monkeypatch.setenv("SNAPGATE_ADMIN_EMAILS", SITE_ADMIN_EMAIL)
    monkeypatch.setattr(sg_settings, "SNAPGATE_SUPERADMIN_EMAILS", ADMIN_EMAIL)
    monkeypatch.setattr(sg_settings, "SNAPGATE_ADMIN_EMAILS", SITE_ADMIN_EMAIL)


@pytest.fixture(autouse=True)
def settings(monkeypatch, tmp_path):
    isolate_env(monkeypatch, tmp_path)
    get_settings.cache_clear()
    project_data.clear_cache()
    yield get_settings()
    get_settings.cache_clear()
    project_data.clear_cache()


@pytest.fixture
def engine(pg_engine):
    clear_verified_cache()   # snapgate caches verified keys per process; schemas reuse ids
    yield pg_engine
    clear_verified_cache()


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture
def app(engine, settings):
    return create_app(engine=engine, settings=settings)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_user(db):
    def _make(email: str, status: UserStatus = UserStatus.APPROVED, name: str | None = None) -> User:
        user = User(google_id=f"google-{uuid.uuid4().hex}", email=email,
                    name=name or email.split("@")[0], status=status)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    return _make


@pytest.fixture
def auth_headers():
    def _headers(user: User) -> dict:
        token, _ = create_access_token(user.id, user.email)
        return {"Authorization": f"Bearer {token}"}
    return _headers


@pytest.fixture
def api_key_headers(db):
    def _headers(user: User, scopes=DEFAULT_SCOPES) -> dict:
        _, key = create_api_key(db, user.id, "test key", key_prefix=API_KEY_PREFIX,
                                scopes=sorted(SCOPES.expand(scopes)))
        return {"Authorization": f"Bearer {key}"}
    return _headers


@pytest.fixture
def toy_root(tmp_path):
    root = tmp_path / "toy"
    shutil.copytree(TOY, root)
    return root


@pytest.fixture
def make_project(engine):
    def _make(slug, members=None, name=None):
        p = pg_projects.create_project(engine, slug, name or slug.title())
        for user, role in (members or {}).items():
            project_svc.set_member(engine, roles.OPERATOR, slug, user.id, role)
        return p
    return _make


@pytest.fixture
def toy_project(engine, make_project, toy_root):
    """Project `toy`, published from a copy of tests/fixtures/toy."""
    p = make_project("toy", name="Toy schema")
    publish(engine, "toy", toy_root)
    return p


@pytest.fixture
def toy_hash(engine, toy_project):
    """sid -> the subject's hash in the current toy snapshot."""
    def _hash(sid):
        with engine.connect() as conn:
            return project_data.load(conn, "toy").subjects.get(sid)["hash"]
    return _hash


@pytest.fixture
def team(make_user, engine, toy_project):
    """Approved accounts on the toy team: a curator, two voters, an outsider,
    and the superadmin (not on the team)."""
    from types import SimpleNamespace
    t = SimpleNamespace(curator=make_user("cur@example.org", name="Cora Curator"),
                        voter=make_user("vot@example.org", name="Vic Voter"),
                        voter2=make_user("vot2@example.org", name="Val Voter"),
                        outsider=make_user("out@example.org", name="Oz Outsider"),
                        admin=make_user(ADMIN_EMAIL, name="Ada Admin"))
    for user, role in ((t.curator, "curator"), (t.voter, "voter"), (t.voter2, "voter")):
        project_svc.set_member(engine, roles.OPERATOR, "toy", user.id, role)
    return t
