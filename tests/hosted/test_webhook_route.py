"""The webhook route passes headers and the raw body through plan 4's handler."""
import json

import pytest
from sqlalchemy import update

from curation_workbench.hosted.sync import webhook
from curation_workbench.hosted.sync.tables import cw_residents

pytestmark = pytest.mark.postgres


def test_webhook_route(client, engine, toy_project):
    with engine.begin() as conn:   # make the dev resident look like a real one with a secret
        conn.execute(update(cw_residents).values(enabled=True, webhook_secret="s3cret",
                                                 repo_url="https://github.com/x/toy", branch="main"))
    url = "/api/v1/hooks/github/toy"
    body = json.dumps({"zen": "hi"}).encode()
    r = client.post(url, content=body, headers={"X-GitHub-Event": "ping",
                                                "X-Hub-Signature-256": "sha256=bad"})
    assert r.status_code == 401
    r = client.post(url, content=body, headers={"X-GitHub-Event": "ping",
                                                "X-Hub-Signature-256": webhook.sign("s3cret", body)})
    assert r.status_code == 200 and r.json() == {"ok": True}
    push = json.dumps({"ref": "refs/heads/main"}).encode()
    r = client.post(url, content=push, headers={"X-GitHub-Event": "push",
                                                "X-Hub-Signature-256": webhook.sign("s3cret", push)})
    assert r.status_code == 202 and r.json() == {"queued": True}
    assert client.post("/api/v1/hooks/github/nope", content=body).status_code == 404
