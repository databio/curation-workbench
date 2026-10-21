"""The hosted HTTP API: every `/api/v1` router (see hosted/API.md).

`mount_api` includes them in `ROUTERS` order. The app factory calls it after
the snapgate routers and before the SPA fallback, so explicit routes always
win over the SPA catch-all.
"""
from __future__ import annotations

from fastapi import APIRouter, FastAPI

from . import admin, data, decisions, hooks, me, projects, votes

API_PREFIX = "/api/v1"

ROUTERS: list[tuple[APIRouter, str]] = [
    (me.router, API_PREFIX),
    (projects.router, API_PREFIX),
    (data.router, API_PREFIX),
    (decisions.router, API_PREFIX),
    (votes.router, API_PREFIX),
    (admin.router, API_PREFIX),
    (hooks.router, API_PREFIX),
]


def mount_api(app: FastAPI) -> None:
    for router, prefix in ROUTERS:
        app.include_router(router, prefix=prefix)


__all__ = ["API_PREFIX", "ROUTERS", "mount_api"]
