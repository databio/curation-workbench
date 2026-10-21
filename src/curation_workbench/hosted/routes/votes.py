"""Votes and questions: `/api/v1/projects/{slug}/votes`, `/questions[...]`."""
from __future__ import annotations

from fastapi import APIRouter, Body, Query, Request, Response

from ..auth import CurrentCaller
from ..service import votes as svc

router = APIRouter(tags=["votes"])
P = "/projects/{slug}"


@router.get(P + "/questions")
def questions(request: Request, caller: CurrentCaller, slug: str, review_type: str | None = None,
              state: str | None = None, sid: str | None = None) -> dict:
    return svc.questions(request.app.state.engine, caller, slug, review_type=review_type,
                         state=state, sid=sid)


@router.get(P + "/votes")
def votes(request: Request, caller: CurrentCaller, slug: str, sid: str = Query(...),
          review_type: str = Query(...)) -> dict:
    return svc.votes(request.app.state.engine, caller, slug, sid, review_type)


@router.put(P + "/votes")
def cast(request: Request, caller: CurrentCaller, slug: str, body: dict = Body(...)) -> dict:
    return svc.cast(request.app.state.engine, caller, slug, body)


@router.delete(P + "/votes", status_code=204, response_class=Response)
def retract(request: Request, caller: CurrentCaller, slug: str, sid: str = Query(...),
            review_type: str = Query(...)) -> Response:
    svc.retract(request.app.state.engine, caller, slug, sid, review_type)
    return Response(status_code=204)


@router.post(P + "/questions/resolve")
def resolve(request: Request, caller: CurrentCaller, slug: str, body: dict = Body(...)) -> dict:
    return svc.resolve(request.app.state.engine, caller, slug, body)


@router.post(P + "/questions/reopen")
def reopen(request: Request, caller: CurrentCaller, slug: str, body: dict = Body(...)) -> dict:
    return svc.reopen(request.app.state.engine, caller, slug, body)
