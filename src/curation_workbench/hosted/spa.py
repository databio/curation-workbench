"""SPA deep-link fallback (copied from Pigeon's spa.py).

Invariant: the origin must hand out ``index.html`` for a browser navigation to
a client-side (history-API) route, and must NOT hand it out for anything else
-- a missing asset or an unmatched API path must stay a real 404. The SPA
routes with react-router v7's ``createBrowserRouter``, so paths like
``/p/onga/history`` are real history-API paths the browser requests directly on
refresh, and plain ``StaticFiles(html=True)`` 404s anything that is not a file
or a directory with an ``index.html``.

Client routes (frontend/src/hosted.tsx): `/`, `/login`, `/pending-approval`,
`/admin/accounts` and `/p/<project>/...`. A new backend prefix goes in
:data:`RESERVED_PREFIXES`.
"""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

#: First path segments the backend owns, never handed the SPA shell, so a
#: browser typing ``/api/bogus`` still gets a 404 instead of the shell.
RESERVED_PREFIXES = frozenset(
    {
        "api",
        "auth",
        "health",
        "docs",
        "redoc",
        "openapi.json",
        "assets",
    }
)


def _wants_spa_shell(path: str, scope) -> bool:
    """Whether a 404 on ``path`` should fall back to ``index.html``."""
    # A POST/PUT/etc to an unknown path keeps its 404/405 -- only a document
    # navigation (GET/HEAD) can plausibly be a client-side route.
    if scope["method"] not in ("GET", "HEAD"):
        return False

    # A browser document navigation sends `Accept: text/html,...`; a
    # <script>/fetch for an asset sends `*/*` or a specific type, so this is
    # what keeps a missing asset a real 404.
    accept = Headers(scope=scope).get("accept", "")
    if "text/html" not in accept:
        return False

    first_segment = path.split("/", 1)[0]
    if first_segment in RESERVED_PREFIXES:
        return False

    return True


class SPAStaticFiles(StaticFiles):
    """``StaticFiles(html=True)`` that falls back to ``index.html`` for an
    unmatched browser navigation, so a refresh on a react-router path
    (``/p/onga/review/...``) resolves instead of 404ing.

    Also sets cache headers: Vite content-hashes everything under
    ``/assets/*``, so those get a long-lived immutable cache; ``index.html``
    (served directly, or as the SPA fallback) gets ``no-cache`` so a deploy
    that changes the asset hashes is picked up on the next navigation instead
    of serving a shell that points at deleted files.
    """

    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or not _wants_spa_shell(path, scope):
                raise
            response = await super().get_response("index.html", scope)
            response.headers["Cache-Control"] = "no-cache"
            return response

        if path in ("index.html", "", "."):
            response.headers["Cache-Control"] = "no-cache"
        elif path.startswith("assets/") or path.startswith("assets\\"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
