"""Read routes: the same bodies the local daemon returns, built by `views`.

Each function checks read access, loads the project's published data
(`project_data.load_for`) and calls the `curation_workbench.views` function
the local daemon calls, with `records` = `derive.merge_decisions(repo
decisions from the snapshot, hosted decisions)`. Hosted records carry
`plugin_errors` from the worker's `decision_checks`.
"""
from __future__ import annotations

from snapgate import Caller, NotFound

from ... import derive, views
from ...store.pg import PgStore
from .. import project_data
from . import roles


def with_plugin_errors(records, checks) -> list[dict]:
    return [{**r, "plugin_errors": list((checks or {}).get(r["id"]) or [])} for r in records]


def _read(engine, caller, slug, fn):
    with engine.connect() as conn:
        project, role = roles.access(conn, caller, slug)
        pd = project_data.load_for(conn, project)
        return fn(conn, pd)


def hosted_records(conn, pd) -> list[dict]:
    store = PgStore(conn.engine, pd.project["id"])
    with store.using(conn):
        return with_plugin_errors(store.records(), pd.decision_checks)


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


def pending(engine, caller: Caller, slug: str) -> dict:
    """Pending hosted decisions with the worker's plugin check errors. Hosted
    mode never dry-runs apply, so there is no `files` key."""
    def fn(conn, pd):
        errors = {k: list(v) for k, v in (pd.decision_checks or {}).items() if v}
        body = views.pending_view(hosted_records(conn, pd), errors=errors)
        body.pop("files", None)
        return body
    return _read(engine, caller, slug, fn)


__all__ = ["all_records", "findings", "history", "hosted_records", "pending", "review_index",
           "review_type", "subject", "subject_index", "verdicts", "with_plugin_errors"]
