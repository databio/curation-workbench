"""Read routes, built by `views`.

Each function checks read access, loads the project's published data
(`project_data.load_for`) and calls a `curation_workbench.views` function,
with `records` = `derive.merge_decisions(repo
decisions from the snapshot, hosted decisions)`.
"""
from __future__ import annotations

from snapgate import Caller, NotFound

from ... import derive, views
from ...store.pg import PgStore
from .. import project_data
from ..sync.repos import repo_web_url
from . import roles


def _read(engine, caller, slug, fn):
    with engine.connect() as conn:
        project, role = roles.access(conn, caller, slug)
        pd = project_data.load_for(conn, project)
        return fn(conn, pd)


def hosted_records(conn, pd) -> list[dict]:
    store = PgStore(conn.engine, pd.project["id"])
    with store.using(conn):
        return store.records()


def all_records(conn, pd) -> list[dict]:
    return derive.merge_decisions(pd.bundle.get("repo_decisions") or [], hosted_records(conn, pd))


def review_index(engine, caller: Caller, slug: str) -> dict:
    def fn(conn, pd):
        body = views.review_index(pd.review)
        if body is None:
            raise NotFound(f"project {slug} has no review types")
        return body
    return _read(engine, caller, slug, fn)


def review_type(engine, caller: Caller, slug: str, track: str, type_: str) -> dict:
    def fn(conn, pd):
        body = views.review_type_page(pd.review, track, type_)
        if body is None:
            raise NotFound(f"no review type {track}/{type_}")
        return body
    return _read(engine, caller, slug, fn)


def subject_index(engine, caller: Caller, slug: str) -> dict:
    return _read(engine, caller, slug, lambda conn, pd: views.subject_index(pd.bundle, pd.status))


def subject(engine, caller: Caller, slug: str, sid: str) -> dict:
    from .votes import subject_votes

    def fn(conn, pd):
        body = views.subject_view(pd.bundle, pd.status, all_records(conn, pd), sid)
        if body is None:
            raise NotFound(f"unknown subject {sid!r}")
        body["votes"] = subject_votes(conn, pd.project["id"], sid)
        return body
    return _read(engine, caller, slug, fn)


def verdicts(engine, caller: Caller, slug: str) -> dict:
    return _read(engine, caller, slug, lambda conn, pd: views.verdicts_view(pd.bundle.get("verdicts")))


def findings(engine, caller: Caller, slug: str) -> dict:
    return _read(engine, caller, slug, lambda conn, pd: views.findings_view(pd.bundle, pd.status))


def history(engine, caller: Caller, slug: str) -> dict:
    return _read(engine, caller, slug, lambda conn, pd: views.history(all_records(conn, pd), pd.bundle))


#: The workflow file a project repo adds to pull approved decisions (ONGA's
#: `.github/workflows/curate.yml`); the Approved page links to its runs.
WORKFLOW_FILE = "curate.yml"
RECENT_APPLIED = 20


def repository(pd) -> dict:
    """Where approved decisions go: the project's GitHub repo, its Actions
    workflow page and the commit the site last loaded."""
    web = repo_web_url(pd.repo)
    return {"url": web,
            "workflow_url": f"{web}/actions/workflows/{WORKFLOW_FILE}" if web else None,
            "commit": pd.commit, "commit_url": f"{web}/commit/{pd.commit}" if web and pd.commit else None,
            "branch": pd.branch, "synced_at": pd.synced_at}


def pending(engine, caller: Caller, slug: str) -> dict:
    """Approved hosted decisions waiting to reach the repo (`views.pending_view`;
    no `files`: the site never runs the apply engine), plus `applied` (the latest hosted decisions the repo now
    holds, newest first) and `repository` (where they go)."""
    def fn(conn, pd):
        records = hosted_records(conn, pd)
        body = views.pending_view(records)
        body.pop("files", None)
        applied = [r for r in records if r.get("status") == "applied"]
        applied.sort(key=lambda r: ((r.get("applied") or {}).get("on") or "", r["id"]), reverse=True)
        body["applied"] = applied[:RECENT_APPLIED]
        body["repository"] = repository(pd)
        return body
    return _read(engine, caller, slug, fn)


__all__ = ["WORKFLOW_FILE", "all_records", "findings", "history", "hosted_records", "pending",
           "repository", "review_index",
           "review_type", "subject", "subject_index", "verdicts"]
