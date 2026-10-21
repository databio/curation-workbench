# SNAPGATE module - do not edit. Overwritten on updates. Customize via SnapgateHooks.
"""The service-layer contract shared by snapgate apps.

Functions hold all logic and all permission checks. They take a :class:`Caller`
and raise the typed errors below. HTTP routes and MCP tools are thin adapters:
they build the Caller, convert input, call the function, and map errors with
the helpers here. A tool never calls the app's own HTTP API (no loopback).

One set of codes for both adapters (HTTP status, wire ``code``, and the MCP
tool text ``"<status>: <detail> (<code>)"``):

- ``NotFound``: 404 ``not_found``
- ``Forbidden`` (object level): 403 ``forbidden``
- ``InsufficientScope``: 403 ``insufficient_scope``. Over OAuth, MCP answers
  it with an HTTP 403 challenge before the exchange starts
  (:func:`mcp_scope_challenge`) so the chat app asks the person again.
- ``Conflict``: 409 ``conflict``
- ``Invalid``: 422 ``invalid_input``
- ``RateLimited``: 429 ``rate_limited``, plus ``Retry-After`` over HTTP

An app subclasses these for its own domain errors. A subclass that does not
set ``code`` gets ``snake(ClassName)`` (``GroupNotFound`` -> ``group_not_found``).

No import of ``mcp`` here, and FastAPI/Starlette only inside the adapter
helpers, so service functions can import this module freely.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal

from .api_keys import MatchedKey
from .models import User
from .scopes import ScopeConfig

#: How the caller signed in: a browser session (JWT), a Settings-page key, or
#: a key an OAuth connection minted (a chat app's connector).
Via = Literal["session", "key", "connection"]


@dataclass(frozen=True)
class Caller:
    """Who is asking and what they may do. Built once per request by an adapter.

    ``scopes`` is already expanded through ``ScopeConfig.implies``, so a check
    is a plain set test.
    """

    user: User
    scopes: frozenset[str]
    via: Via = "session"

    def require(self, *needed: str) -> None:
        """Raise :class:`InsufficientScope` unless every name in ``needed`` is held."""
        if frozenset(needed) - self.scopes:
            raise InsufficientScope(needed)


def caller_for(user: User, key: MatchedKey | None, scopes: ScopeConfig) -> Caller:
    """A browser session (no key) holds every scope; a key holds its own, expanded."""
    if key is None:
        return Caller(user, scopes.names, "session")
    via: Via = "connection" if key.connection_id is not None else "key"
    return Caller(user, scopes.expand(key.scopes), via)


_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


class ServiceError(Exception):
    """Base for typed errors.

    ``status`` is the HTTP status; ``code`` is the wire code both adapters show.
    A subclass without its own ``code`` gets ``snake(ClassName)``. ``detail`` is
    the plain-language message (``str(exc)`` gives it too). Keyword arguments
    land in ``data``: extra fields for the HTTP body (``current_version=``,
    ``valid=``).
    """

    status: int = 500
    code: str = "service_error"

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        if "code" not in cls.__dict__:
            cls.code = _CAMEL.sub("_", cls.__name__).lower()

    def __init__(self, detail: str = "", **data: Any) -> None:
        super().__init__(detail)
        self.detail = detail or type(self).__name__
        self.data = data


class NotFound(ServiceError):
    """404: no such thing, or the caller may not know it exists."""

    status, code = 404, "not_found"


class Forbidden(ServiceError):
    """403: an object-level refusal ("only a group owner can do that")."""

    status, code = 403, "forbidden"


class Conflict(ServiceError):
    """409: the state does not allow it (stale version, already exists)."""

    status, code = 409, "conflict"


class Invalid(ServiceError):
    """422: bad input. ``field`` names the argument when there is one."""

    status, code = 422, "invalid_input"

    def __init__(self, detail: str, *, field: str | None = None, **data: Any) -> None:
        super().__init__(detail, **data)
        self.field = field


class InsufficientScope(ServiceError):
    """403: the credential lacks a scope (a key or connection, never a session)."""

    status, code = 403, "insufficient_scope"

    def __init__(
        self, needed: Iterable[str], detail: str = "This key is not allowed to do that."
    ) -> None:
        super().__init__(detail)
        self.needed = frozenset(needed)


class RateLimited(ServiceError):
    """429: too many; ``retry_after`` is whole seconds (at least 1)."""

    status, code = 429, "rate_limited"

    def __init__(self, retry_after: float, detail: str = "too many requests; retry later") -> None:
        super().__init__(detail)
        self.retry_after = max(1, int(retry_after))


# -- HTTP adapter ------------------------------------------------------------------


def http_headers(exc: ServiceError, request=None) -> dict[str, str]:
    """The headers an HTTP reply to ``exc`` carries.

    ``InsufficientScope`` gets the same ``WWW-Authenticate`` challenge as
    :class:`snapgate.InsufficientScopeError` (with ``resource_metadata`` when the
    OAuth server is installed); ``RateLimited`` gets ``Retry-After``.
    """
    if isinstance(exc, InsufficientScope):
        from .scopes import _insufficient_scope_challenge

        return {"WWW-Authenticate": _insufficient_scope_challenge(exc.needed, request)}
    if isinstance(exc, RateLimited):
        return {"Retry-After": str(exc.retry_after)}
    return {}


def default_body(exc: ServiceError) -> dict:
    """``{"detail", "code"}``, plus ``field`` for :class:`Invalid`, plus ``exc.data``."""
    body: dict[str, Any] = {"detail": exc.detail, "code": exc.code}
    if isinstance(exc, Invalid) and exc.field:
        body["field"] = exc.field
    return body | exc.data


def install_service_errors(app, *, body: Callable[[ServiceError], dict] = default_body) -> None:
    """HTTP adapter: one handler maps every :class:`ServiceError` to status, headers, body.

    Pass ``body`` to shape the JSON differently; status and headers stay snapgate's.
    """
    from fastapi.responses import JSONResponse

    def handle(request, exc: ServiceError):
        return JSONResponse(
            body(exc), status_code=int(exc.status), headers=http_headers(exc, request)
        )

    app.add_exception_handler(ServiceError, handle)


# -- MCP adapter -------------------------------------------------------------------


def tool_error_text(exc: ServiceError) -> str:
    """The text an agent reads in an ``isError`` tool result: ``"404: <detail> (not_found)"``.

    The app raises its SDK's tool error with it (``raise ToolError(tool_error_text(e))``).
    """
    return f"{int(exc.status)}: {exc.detail} ({exc.code})"


def mcp_scope_challenge(
    request, needed: Iterable[str], detail: str, *, description: str | None = None
):
    """The HTTP 403 ``insufficient_scope`` reply for an MCP request over OAuth.

    Sent before the MCP exchange starts (a tool cannot change the HTTP status of
    the exchange it runs in). Its ``WWW-Authenticate`` names ``needed`` and points
    at the Protected Resource Metadata, which makes a chat app ask the person
    again for more access. ``detail`` is the JSON body's message;
    ``description`` (default ``detail``) is the challenge's
    ``error_description`` and must be plain ASCII without quotes. Needs
    :func:`snapgate.create_oauth_router` to have been called.
    """
    from starlette.responses import JSONResponse

    from .oauth import www_authenticate

    challenge = www_authenticate(
        request,
        error="insufficient_scope",
        description=detail if description is None else description,
        scope=sorted(needed),
    )
    return JSONResponse(
        {"detail": detail}, status_code=403, headers={"WWW-Authenticate": challenge}
    )


__all__ = [
    "Caller",
    "Conflict",
    "Forbidden",
    "InsufficientScope",
    "Invalid",
    "NotFound",
    "RateLimited",
    "ServiceError",
    "Via",
    "caller_for",
    "default_body",
    "http_headers",
    "install_service_errors",
    "mcp_scope_challenge",
    "tool_error_text",
]
