"""`POST /api/v1/hooks/github/{slug}`: GitHub push webhook (no sign-in).

Passes the headers and raw body to plan 4's `hosted.sync.webhook.handle`,
which checks the HMAC signature, answers `ping`, limits each resident to 30
requests a minute, and queues a commit job.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..sync import webhook

router = APIRouter(tags=["hooks"])


@router.post("/hooks/github/{slug}")
async def github(request: Request, slug: str) -> JSONResponse:
    body = await request.body()
    engine = request.app.state.engine

    def run():
        with engine.begin() as conn:
            return webhook.handle(conn, slug, request.headers, body)

    from starlette.concurrency import run_in_threadpool
    status, out = await run_in_threadpool(run)
    return JSONResponse(out, status_code=status)
