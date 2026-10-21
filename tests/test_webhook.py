"""GitHub push webhook: signature, ping, other branches, one job per burst."""
import json

import pytest
from sqlalchemy import func, select

from curation_workbench.hosted.sync import residents, webhook
from curation_workbench.hosted.sync.tables import cw_sync_jobs

pytestmark = pytest.mark.postgres


@pytest.fixture
def hook(sync_env):
    secret = residents.add(sync_env.engine, residents.OPERATOR, "toy", sync_env.url, "master",
                           settings=sync_env.settings)["webhook_secret"]
    with sync_env.engine.begin() as conn:      # drop the job `add` queued
        conn.execute(cw_sync_jobs.delete())
    webhook._hits.clear()

    def call(event, payload, sign_with=secret, slug="toy"):
        body = json.dumps(payload).encode()
        headers = {"X-GitHub-Event": event,
                   "X-Hub-Signature-256": webhook.sign(sign_with, body) if sign_with else ""}
        with sync_env.engine.begin() as conn:
            return webhook.handle(conn, slug, headers, body)
    return sync_env, call


def queued(engine):
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(cw_sync_jobs).where(
            cw_sync_jobs.c.status == "queued")).scalar_one()


def test_signature_ping_branch_and_dedupe(hook):
    env, call = hook
    push = {"ref": "refs/heads/master", "after": "f" * 40}
    assert call("push", push, sign_with="wrong")[0] == 401
    assert call("push", push, sign_with=None)[0] == 401
    assert call("ping", {"zen": "hi"}) == (200, {"ok": True})
    assert call("issues", {})[0] == 400
    assert call("push", {"ref": "refs/heads/other"})[0] == 202
    assert queued(env.engine) == 0
    assert call("push", push) == (202, {"queued": True})
    assert call("push", push) == (202, {"queued": False})       # one live job per kind
    assert queued(env.engine) == 1
    with env.engine.connect() as conn:
        job = conn.execute(select(cw_sync_jobs)).one()
    assert (job.kind, job.trigger, job.commit_sha) == ("commit", "webhook", None)  # sha never trusted
    assert call("push", push, slug="nope")[0] == 404


def test_rate_limit(hook):
    env, call = hook
    for _ in range(webhook.RATE_LIMIT):
        assert call("ping", {})[0] == 200
    assert call("ping", {})[0] == 429
