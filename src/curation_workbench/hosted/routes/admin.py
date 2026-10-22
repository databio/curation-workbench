"""Superadmin (browser session) admin: projects (with their GitHub repo) and teams.

Accounts (approve, suspend) are snapgate's `/auth/admin/*` routes.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Request, Response

from ..auth import CurrentCaller
from ..service import projects as svc

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


@router.put("/projects/{slug}/repo")
def set_repo(request: Request, caller: CurrentCaller, slug: str, body: dict = Body(...)) -> dict:
    return svc.admin_set_repo(request.app.state.engine, caller, slug, body)


@router.post("/projects/{slug}/sync")
def sync_project(request: Request, caller: CurrentCaller, slug: str) -> dict:
    return svc.admin_sync_project(request.app.state.engine, caller, slug)
