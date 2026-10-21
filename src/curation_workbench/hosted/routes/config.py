"""`GET /health` and `GET /api/config` (no sign-in)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

logger = logging.getLogger("curation_workbench.hosted.health")

router = APIRouter(tags=["config"])


def _check_db(engine) -> tuple[bool, str]:
    try:
        with engine.connect() as conn, conn.begin():
            conn.execute(text("SET LOCAL statement_timeout = 2000"))
            conn.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:  # the class only: no DSN in the body
        logger.warning("health: db check failed: %s", exc)
        return False, f"error: {type(exc).__name__}"


@router.get("/health")
def health(request: Request) -> JSONResponse:
    """`{status: ok|degraded, checks: {db, schema}}`; 200 when all ok, else 503."""
    state = request.app.state
    ok_db, db = _check_db(state.engine)
    schema_error = getattr(state, "schema_error", None)
    checks = {"db": db, "schema": "ok" if schema_error is None else "error: schema drift"}
    ok = ok_db and schema_error is None
    return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks},
                        status_code=200 if ok else 503, headers={"Cache-Control": "no-store"})


@router.get("/api/config")
def config() -> dict:
    """The SPA reads this first to pick its backend."""
    return {"mode": "hosted"}
