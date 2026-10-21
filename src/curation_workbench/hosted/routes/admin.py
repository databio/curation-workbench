"""Superadmin (browser session) admin: projects, teams, read-only residents.

Accounts (approve, suspend) are snapgate's `/auth/admin/*` routes.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Request, Response

from ..auth import CurrentCaller
from ..service import projects as svc
from ..service import roles

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/projects")
def list_projects(request: Request, caller: CurrentCaller) -> dict:
    return svc.admin_list_projects(request.app.state.engine, caller)


@router.post("/projects", status_code=201)
def create_project(request: Request, caller: CurrentCaller, body: dict = Body(...)) -> dict:
    return svc.admin_create_project(request.app.state.engine, caller, body)


@router.patch("/projects/{slug}")
def rename_project(request: Request, caller: CurrentCaller, slug: str, body: dict = Body(...)) -> dict:
    return svc.admin_rename_project(request.app.state.engine, caller, slug, body)


@router.delete("/projects/{slug}")
def archive_project(request: Request, caller: CurrentCaller, slug: str) -> dict:
    return svc.admin_archive_project(request.app.state.engine, caller, slug)


@router.put("/projects/{slug}/team/{user_id}")
def set_member(request: Request, caller: CurrentCaller, slug: str, user_id: int,
               body: dict = Body(...)) -> dict:
    return svc.set_member(request.app.state.engine, caller, slug, user_id, body.get("role"))


@router.delete("/projects/{slug}/team/{user_id}", status_code=204, response_class=Response)
def remove_member(request: Request, caller: CurrentCaller, slug: str, user_id: int) -> Response:
    svc.remove_member(request.app.state.engine, caller, slug, user_id)
    return Response(status_code=204)


def _resident_caller(caller):
    from ..sync.residents import Caller as ResidentCaller
    roles.require_superadmin(caller)
    return ResidentCaller(is_superadmin=True, user_id=caller.user.id, label=caller.user.email)


@router.get("/residents")
def list_residents(request: Request, caller: CurrentCaller) -> dict:
    from ..sync import residents
    return {"residents": residents.list_residents(request.app.state.engine, _resident_caller(caller))}


@router.get("/residents/{slug}")
def show_resident(request: Request, caller: CurrentCaller, slug: str) -> dict:
    from ..sync import residents
    return residents.show(request.app.state.engine, _resident_caller(caller), slug)
