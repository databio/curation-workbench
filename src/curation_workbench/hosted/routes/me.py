"""`GET /api/v1/me`."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..auth import CurrentCaller
from ..service import projects as svc

router = APIRouter(tags=["me"])


@router.get("/me")
def me(request: Request, caller: CurrentCaller) -> dict:
    """The signed-in account, its site role (browser session only) and its
    teams: `{user: {id, name, email}, site_role, memberships: {slug: role}}`."""
    return svc.me(request.app.state.engine, caller)
