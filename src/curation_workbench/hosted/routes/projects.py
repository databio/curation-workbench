"""`/api/v1/projects[/{slug}[/team]]`."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..auth import CurrentCaller
from ..service import projects as svc

router = APIRouter(tags=["projects"])


@router.get("/projects")
def list_projects(request: Request, caller: CurrentCaller) -> dict:
    return svc.list_projects(request.app.state.engine, caller)


@router.get("/projects/{slug}")
def project(request: Request, caller: CurrentCaller, slug: str) -> dict:
    return svc.project_detail(request.app.state.engine, caller, slug)


@router.get("/projects/{slug}/team")
def team(request: Request, caller: CurrentCaller, slug: str) -> dict:
    return svc.team(request.app.state.engine, caller, slug)
