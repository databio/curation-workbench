"""The hosted API's app factory: `create_app(engine=, settings=)`.

Modeled on Pigeon's. It builds a fully wired app from what it is given (tests
pass a migrated throwaway-schema engine). The module-level `app` for uvicorn
lives in `hosted.asgi`.

Middleware order (snapgate canonical): SessionMiddleware (OAuth state/PKCE),
SlowAPIMiddleware (snapgate's login limits), CORSMiddleware. The lifespan
refuses to start on schema drift (`assert_schema_current` over every table:
plan 1's, snapgate's, this package's and the repo snapshot tables). It also
starts the repo poller (`sync.repos.Poller`, every `CWB_FETCH_SECONDS`), which
fetches each project's committed snapshot from GitHub.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from snapgate import cleanup_expired_codes, limiter
from snapgate import settings as sg_settings

from .. import __version__
from ..store.pg import database_url_from_env, make_engine
from ..store.pg.migrate import SchemaDriftError, assert_schema_current
from . import tables
from .auth import build_auth_router, build_user_admin, build_useradmin_router
from .config import Settings, get_settings
from .ratelimit import RateLimitConfig, SlidingWindowLimiter
from .routes import mount_api
from .routes.config import router as config_router
from .routes.errors import install_error_handlers
from .security import validate_production_security
from .spa import SPAStaticFiles
from .sync import tables as sync_tables

logger = logging.getLogger("curation_workbench.hosted.app")

API_DESCRIPTION = """Hosted curation: review a LinkML schema together.

People sign in with Prosopia. New accounts wait until a superadmin approves
them; the superadmin then puts each person on a project's curation team as a
**voter** or a **curator**. Voters vote on review questions; curators read the
tallies and turn them into decisions. Votes never become decisions on their own.

**Auth.** Every `/api/v1` route needs a browser
session or `Authorization: Bearer cwb_<key>` (Settings -> API keys).

**Scopes.** A key holds some of these (a browser session holds all):
`curation:read`, `curation:vote` (includes read), `curation:decide` (includes
read; curators only). A missing scope is 403 `insufficient_scope`. Project
and team admin is superadmin-session only.

**Errors.** `{"detail": "...", "code": "snake_case"}`; store errors add
`errors` (every message) and, on a 409, `current`.
"""


def schema_metadata() -> tuple:
    """Every table the service needs, besides plan 1's (the drift check)."""
    import snapgate.useradmin  # noqa: F401  (registers its tables on SQLModel.metadata)
    return (SQLModel.metadata, tables.METADATA, sync_tables.METADATA)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    engine: Engine = app.state.engine
    try:
        assert_schema_current(engine, schema_metadata())
    except SchemaDriftError as exc:
        app.state.schema_error = str(exc)
        logger.critical("%s", exc)
        raise
    with Session(engine) as session:
        n = cleanup_expired_codes(session)
        if n:
            logger.info("startup: removed %d expired exchange codes", n)
    poller = None
    seconds = app.state.settings.fetch_interval
    if seconds:
        from .sync.repos import Poller
        poller = Poller(engine, seconds).start()
        logger.info("repo poller started: every %d s", seconds)
    yield
    if poller is not None:
        poller.stop()
    try:
        with Session(engine) as session:
            cleanup_expired_codes(session)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("shutdown cleanup failed: %s", exc)


def _mount_spa(app: FastAPI, settings: Settings) -> None:
    dist = settings.frontend_dist
    if dist is not None and (dist / "index.html").is_file():
        app.mount("/", SPAStaticFiles(directory=dist, html=True), name="spa")
        return

    @app.get("/", include_in_schema=False)
    def _no_frontend() -> JSONResponse:
        return JSONResponse({"service": "curation-workbench", "version": __version__,
                             "docs": "/docs", "health": "/health"})


def create_app(*, engine: Engine | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    validate_production_security(sg_settings, settings)
    engine = engine or make_engine(database_url_from_env())

    app = FastAPI(title="curation-workbench", version=__version__, description=API_DESCRIPTION,
                  lifespan=_lifespan, redirect_slashes=False)
    app.state.engine = engine
    app.state.settings = settings
    app.state.schema_error = None
    app.state.limits = SlidingWindowLimiter(RateLimitConfig.from_env(
        enabled=settings.rate_limits, client_ip_header=settings.client_ip_header))

    app.state.limiter = limiter  # snapgate's slowapi limiter (its own routes count on it)
    app.add_middleware(SessionMiddleware, secret_key=sg_settings.SESSION_SECRET_KEY,
                       https_only=settings.production, same_site="lax")
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=sg_settings.allowed_origins,
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    install_error_handlers(app)

    app.include_router(config_router)                       # /health, /api/config
    user_admin = build_user_admin(settings)
    app.state.user_admin = user_admin
    app.include_router(build_auth_router(user_admin))       # snapgate at /auth/*
    app.include_router(build_useradmin_router(user_admin))  # snapgate user admin
    mount_api(app)                                          # /api/v1
    _mount_spa(app, settings)                               # LAST: it catches everything else
    return app


__all__ = ["create_app", "schema_metadata"]
