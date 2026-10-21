"""`/api/v1/projects/{slug}/decisions[...]`."""
from __future__ import annotations

from fastapi import APIRouter, Body, Header, Request

from ..auth import CurrentCaller
from ..service import decisions as svc

router = APIRouter(tags=["decisions"])
P = "/projects/{slug}/decisions"


@router.get(P)
def list_decisions(request: Request, caller: CurrentCaller, slug: str,
                   status: str | None = None) -> dict:
    return svc.list_decisions(request.app.state.engine, caller, slug, status=status)


@router.get(P + "/export")
def export(request: Request, caller: CurrentCaller, slug: str) -> dict:
    return svc.export(request.app.state.engine, caller, slug)


@router.get(P + "/{dec_id}")
def get_decision(request: Request, caller: CurrentCaller, slug: str, dec_id: str) -> dict:
    return svc.get_decision(request.app.state.engine, caller, slug, dec_id)


@router.post(P, status_code=201)
def create(request: Request, caller: CurrentCaller, slug: str, body: dict = Body(...)) -> dict:
    return svc.create(request.app.state.engine, caller, slug, body)


@router.put(P + "/{dec_id}")
def update(request: Request, caller: CurrentCaller, slug: str, dec_id: str,
           body: dict = Body(...), if_match: str | None = Header(None)) -> dict:
    return svc.update(request.app.state.engine, caller, slug, dec_id, body, if_match)


@router.delete(P + "/{dec_id}")
def withdraw(request: Request, caller: CurrentCaller, slug: str, dec_id: str,
             if_match: str | None = Header(None)) -> dict:
    return svc.withdraw(request.app.state.engine, caller, slug, dec_id, if_match)
