"""Database sessions for the hosted service.

One engine per app (`app.state.engine`, built with plan 1's `make_engine`).
`get_session` is snapgate's `get_db` and the routes' session dependency;
service functions use the engine directly (Core queries, `PgStore`).
"""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.engine import Engine
from sqlmodel import Session


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency: a SQLModel session on `request.app.state.engine`."""
    with Session(request.app.state.engine) as session:
        yield session


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


__all__ = ["get_engine", "get_session"]
