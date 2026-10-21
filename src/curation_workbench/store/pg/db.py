"""Database wiring: URL from env, engine construction.

Hosted mode is Postgres-only, through psycopg 3 (`postgresql+psycopg://`).
There is no SQLite fallback: with no database configured,
`database_url_from_env` raises.
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

DATABASE_URL_ENV = "CWB_DATABASE_URL"
DRIVER = "postgresql+psycopg"


def database_url_from_env(env: dict[str, str] | None = None) -> str:
    """The SQLAlchemy URL: `CWB_DATABASE_URL`, else composed from `POSTGRES_*`.

    The password is percent-encoded: real passwords may contain URL-special
    characters that would otherwise break the composed URL."""
    env = os.environ if env is None else env
    url = (env.get(DATABASE_URL_ENV) or "").strip()
    if url:
        return url
    database = (env.get("POSTGRES_DB") or "").strip()
    if database:
        user = (env.get("POSTGRES_USER") or "").strip()
        password = quote_plus(env.get("POSTGRES_PASSWORD") or "")
        host = (env.get("POSTGRES_HOST") or "").strip() or "localhost"
        port = (env.get("POSTGRES_PORT") or "").strip() or "5432"
        return f"{DRIVER}://{user}:{password}@{host}:{port}/{database}"
    raise RuntimeError(
        f"no database configured: set {DATABASE_URL_ENV} or POSTGRES_DB/USER/PASSWORD/HOST/PORT "
        f"(locally: scripts/dev-db.sh start, then {DATABASE_URL_ENV}=$(scripts/dev-db.sh url))")


def normalize_url(url: str) -> str:
    """Name the psycopg 3 driver explicitly; refuse psycopg2."""
    scheme, sep, rest = url.partition("://")
    if not sep:
        raise RuntimeError(f"not a database URL: {url!r}")
    if scheme in ("postgres", "postgresql"):
        return f"{DRIVER}://{rest}"
    if scheme == "postgresql+psycopg2":
        raise RuntimeError(
            "postgresql+psycopg2:// is not supported: curation-workbench uses psycopg 3 only "
            "(psycopg2 is not installed). Use postgresql+psycopg:// or postgresql://.")
    if scheme != DRIVER:
        raise RuntimeError(f"unsupported database URL scheme {scheme!r}; use {DRIVER}://")
    return url


def make_engine(url: str, **kwargs: Any) -> Engine:
    opts: dict[str, Any] = {"pool_pre_ping": True}
    opts.update(kwargs)
    return create_engine(normalize_url(url), **opts)


__all__ = ["DATABASE_URL_ENV", "database_url_from_env", "make_engine", "normalize_url"]
