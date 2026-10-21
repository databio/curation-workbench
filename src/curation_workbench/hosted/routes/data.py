"""Read routes: the same bodies as the local daemon's review-UI routes (`views`).

SIDs contain `:` and may contain `/`, so they go in the query string.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from ..auth import CurrentCaller
from ..service import data as svc

router = APIRouter(tags=["data"])
P = "/projects/{slug}"


@router.get(P + "/review")
def review(request: Request, caller: CurrentCaller, slug: str) -> dict:
    return svc.review_index(request.app.state.engine, caller, slug)


@router.get(P + "/review/{track}/{type_}")
def review_type(request: Request, caller: CurrentCaller, slug: str, track: str, type_: str) -> dict:
    return svc.review_type(request.app.state.engine, caller, slug, track, type_)


@router.get(P + "/subject_index")
def subject_index(request: Request, caller: CurrentCaller, slug: str) -> dict:
    return svc.subject_index(request.app.state.engine, caller, slug)


@router.get(P + "/subject")
def subject(request: Request, caller: CurrentCaller, slug: str, sid: str = Query(...)) -> dict:
    return svc.subject(request.app.state.engine, caller, slug, sid)


@router.get(P + "/verdicts")
def verdicts(request: Request, caller: CurrentCaller, slug: str) -> dict:
    return svc.verdicts(request.app.state.engine, caller, slug)


@router.get(P + "/findings")
def findings(request: Request, caller: CurrentCaller, slug: str) -> dict:
    return svc.findings(request.app.state.engine, caller, slug)


@router.get(P + "/history")
def history(request: Request, caller: CurrentCaller, slug: str) -> dict:
    return svc.history(request.app.state.engine, caller, slug)


@router.get(P + "/pending")
def pending(request: Request, caller: CurrentCaller, slug: str) -> dict:
    return svc.pending(request.app.state.engine, caller, slug)
