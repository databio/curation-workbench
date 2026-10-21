# SNAPGATE module - do not edit. Overwritten on updates. Customize via SnapgateHooks.
"""An optional OAuth authorization server, so chat apps can connect to an MCP server.

A chat app (claude.ai, ChatGPT, Claude Code) given only ``<origin>/mcp`` finds
the rest itself and runs the standard MCP authorization flow:

1. ``/mcp`` without a key answers 401 with ``WWW-Authenticate: Bearer
   resource_metadata=..., scope="..."`` (build it with :func:`www_authenticate`),
   pointing at ``/.well-known/oauth-protected-resource`` (RFC 9728), which names
   this origin as the authorization server.
2. ``/.well-known/oauth-authorization-server`` (RFC 8414) lists the endpoints,
   the app's scopes, and ``client_id_metadata_document_supported``.
3. The app identifies itself one of two ways: an ``https`` URL as its client_id
   (a Client ID Metadata Document, :mod:`snapgate.client_metadata`), fetched and
   checked at the consent page; or ``POST /oauth/register`` (RFC 7591) as a
   public client (no secret), which returns an ``sgo_`` id.
4. ``GET /oauth/authorize`` opens in a pop-up. The person signs in if needed
   (a short-lived, consent-only sign-in on the session cookie), then a consent
   page shows the app's name, the website vouching for it, where the person
   will be sent back, and the permissions they may grant, as checkboxes (each
   one the app asked for starts ticked; the person may untick, but must leave
   at least one). Allow stores a
   single-use code (sha256-hashed, ten minutes) and redirects back with it.
   Every authorization response carries ``iss`` (RFC 9207).
5. ``POST /oauth/token`` trades the code and its PKCE (S256) verifier for a
   CONNECTION: a one-hour access token, which is an ordinary snapgate API key
   confined to ``resource_path``, and a refresh token (``sgr_``). A code that
   comes back after it was spent revokes the connection it minted (RFC 6749
   section 4.1.2); spent codes are kept ``code_reuse_window`` past expiry to
   catch that.
6. The refresh token buys a new pair at the same endpoint. Each use retires it
   into ``OAuthRefreshHistory`` with a 120-second grace window for overlapping
   calls. Any token rotated away, presented after its grace window, revokes
   the whole connection (a replayed token means it leaked), however many
   rotations old it is. A connection not refreshed for 90 days lapses. The
   person can change what a connection may do, or revoke it, under
   ``/auth/connections``.

The token endpoint fetches nothing: the code row pins the client_id and
redirect URI, and the app proves possession with PKCE. A connection survives
its metadata document changing or vanishing.

``POST /oauth/revoke`` (RFC 7009) ends a connection given its refresh token.

Native app login (optional, ``OAuthServerConfig.native_client``): the app's
own command-line tool signs a person in with the same flow (RFC 8252,
loopback redirect + PKCE). Its client_id is fixed and never registered; it
may return only to ``http://127.0.0.1:<port>/callback`` or
``http://[::1]:<port>/callback``; it gets exactly its own scopes (never in the
app's ``ScopeConfig``, so no chat app can ask for them) on a fixed consent
page without checkboxes; its keys are confined to its own ``resource_path``.
Each sign-in is its own connection (no step-up revoke), listed under
``/auth/connections`` with ``fixed: true`` (it can be revoked, not changed).

One OAuth config per process: :func:`create_oauth_router` installs its config
module-wide so :func:`www_authenticate` and ``InsufficientScopeError`` can find
the issuer. The last call wins.

The app needs Starlette's ``SessionMiddleware`` (snapgate's sign-in
already does) and must include this router before any SPA catch-all.
"""

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from html import escape
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, update
from sqlmodel import Session, col, select
from starlette.concurrency import run_in_threadpool

from .api_keys import (
    create_api_key,
    prune_connection_keys,
    revoke_connection_keys,
    set_connection_key_scopes,
)
from .client_metadata import (
    ClientMetadataCache,
    ClientMetadataError,
    check_client_id_url,
    fetch_client_metadata,
    is_metadata_client_id,
    redirect_uri_ok,
)
from .config import settings
from .core import AuthError, client_ip, create_auth_dependencies, within_rate_limit
from .models import (
    OAuthClient,
    OAuthCode,
    OAuthConnection,
    OAuthRefreshHistory,
    User,
    UserStatus,
    hash_token,
)
from .scopes import ScopeConfig

AUTHORIZE_PATH = "/oauth/authorize"
TOKEN_PATH = "/oauth/token"
REVOKE_PATH = "/oauth/revoke"
REGISTER_PATH = "/oauth/register"
PROTECTED_RESOURCE_PATH = "/.well-known/oauth-protected-resource"
GRANT_TYPES = ["authorization_code", "refresh_token"]
CLIENT_PREFIX = "sgo_"
CODE_PREFIX = "sgc_"
REFRESH_PREFIX = "sgr_"
CONSENT_USER_KEY = "sg_consent_user"
CSRF_KEY = "sg_consent_csrf"
#: How long a retired refresh token's hash is kept, to recognize a replay.
#: Never less than ``refresh_idle_ttl`` (see ``OAuthServerConfig``).
REFRESH_HISTORY_KEEP = timedelta(days=90)
#: How long a code is kept past its expiry, to recognize reuse.
CODE_REUSE_WINDOW = timedelta(days=1)

_VERIFIER_RE = re.compile(r"[A-Za-z0-9\-._~]{43,128}")  # RFC 7636 section 4.1
_CHALLENGE_RE = re.compile(r"[A-Za-z0-9\-_]{43}")  # base64url(sha256), no padding


@dataclass(frozen=True)
class NativeClient:
    """The app's own command-line tool, signing a person in (RFC 8252).

    Public and fixed: it is never registered and has no metadata document.
    Its scopes must not be in the app's ``ScopeConfig``: that is what keeps a
    chat app from asking for them and lets the app tell this login apart from
    a chat-app connection or a Settings key.
    """

    client_id: str  # e.g. "lumenoia-cli"
    name: str  # e.g. "Lumenoia command line"
    scopes: tuple[str, ...]  # e.g. ("cli:login",); never in OAuthServerConfig.scopes
    resource_path: str  # e.g. "/api"; its keys are confined here
    consent_text: str  # one paragraph on the consent page
    refresh_idle_ttl: timedelta = timedelta(days=30)


_NATIVE_REDIRECT_RE = re.compile(r"http://(127\.0\.0\.1|\[::1\]):([0-9]{1,5})/callback")


def native_redirect_ok(uri: str) -> bool:
    """RFC 8252 section 7.3: ``http://127.0.0.1:<port>/callback`` or
    ``http://[::1]:<port>/callback``, any port. Never ``localhost`` (it can
    resolve elsewhere), another path, a query or a fragment."""
    m = _NATIVE_REDIRECT_RE.fullmatch(uri or "")
    return bool(m) and 0 < int(m.group(2)) < 65536


@dataclass
class OAuthServerConfig:
    """Settings for :func:`create_oauth_router`.

    ``issuer`` must be set in production (e.g. ``"https://deadliner.databio.org"``);
    None falls back to the request's base URL, which trusts the ``Host`` header
    and is for development only.
    """

    app_name: str  # "Deadliner": consent page title and copy
    scopes: ScopeConfig
    resource_path: str = "/mcp"  # the protected resource; connection keys are confined to it
    issuer: str | None = None
    access_key_prefix: str | None = None  # defaults to settings.API_KEY_PREFIX
    connection_note: str | None = None  # extra consent-page paragraph
    connections_location: str = "your Settings page"  # where to change or revoke it
    client_metadata_fetch: Callable | None = None  # test seam: replaces fetch_client_metadata
    code_ttl: timedelta = timedelta(minutes=10)
    access_ttl: timedelta = timedelta(hours=1)
    refresh_idle_ttl: timedelta = timedelta(days=90)
    refresh_grace: timedelta = timedelta(seconds=120)
    access_key_keep: timedelta = timedelta(days=1)
    consent_login_ttl: timedelta = timedelta(minutes=30)
    # Spent codes are kept this long past expiry, so a reuse is still caught.
    code_reuse_window: timedelta = CODE_REUSE_WINDOW
    # Retired refresh-token hashes are kept this long. Raised to
    # refresh_idle_ttl if shorter: a token can be replayed for as long as its
    # connection could still be alive.
    refresh_history_keep: timedelta = REFRESH_HISTORY_KEEP
    #: Where to send a person who must sign in before the consent page.
    #: Called with the consent-page path to return to (always under /oauth/authorize)
    #: and whether to force an account chooser. None = snapgate's own sign-in.
    #: The app's login route verifies the person, then calls
    #: ``safe_consent_next``, ``get_or_create_external_user`` and ``set_consent_user``.
    sign_in: Callable[[str, bool], str] | None = None
    #: The app's own command-line login (RFC 8252), or None. See :class:`NativeClient`.
    native_client: NativeClient | None = None

    def __post_init__(self) -> None:
        keep = [self.refresh_history_keep, self.refresh_idle_ttl]
        native = self.native_client
        if native is not None:
            if not native.scopes:
                raise ValueError("native_client needs at least one scope")
            clash = sorted(set(native.scopes) & set(self.scopes.names))
            if clash:
                raise ValueError(
                    f"native_client scopes must not be chat-app scopes: {', '.join(clash)}"
                )
            if (
                not native.client_id
                or native.client_id.startswith(CLIENT_PREFIX)
                or is_metadata_client_id(native.client_id)
            ):
                raise ValueError(f"unusable native_client client_id: {native.client_id!r}")
            if not native.resource_path.startswith("/"):
                raise ValueError("native_client resource_path must start with /")
            keep.append(native.refresh_idle_ttl)
        self.refresh_history_keep = max(keep)

    def is_native(self, client_id: str | None) -> bool:
        """True for the native client's client_id."""
        return self.native_client is not None and client_id == self.native_client.client_id


#: The installed config (one per process; the last create_oauth_router call wins).
_installed: OAuthServerConfig | None = None
#: Metadata-document cache for the installed config.
_metadata_cache: ClientMetadataCache | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """Databases without TIMESTAMPTZ (SQLite) hand back naive UTC datetimes."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _issuer(config: OAuthServerConfig, request: Request | None) -> str:
    if config.issuer:
        return config.issuer.rstrip("/")
    if request is not None:
        return str(request.base_url).rstrip("/")
    return ""


def www_authenticate(
    request: Request | None,
    *,
    error: str | None = None,
    description: str | None = None,
    scope: Iterable[str] | None = None,
) -> str:
    """The Bearer challenge for a 401 (or 403 ``insufficient_scope``) from the resource.

    ``error`` is RFC 6750's (``invalid_token``, ``insufficient_scope``); leave it
    out when no key was sent at all. ``scope`` defaults to every declared scope.
    ``description`` must be plain ASCII without quotes.
    """
    config = _installed
    if config is None:
        raise RuntimeError("www_authenticate needs create_oauth_router to have been called")
    names = config.scopes.names if scope is None else scope
    parts = [
        f'resource_metadata="{_issuer(config, request)}{PROTECTED_RESOURCE_PATH}'
        f'{config.resource_path}"',
        f'scope="{config.scopes.scope_string(names)}"',
    ]
    if error:
        parts.insert(0, f'error="{error}"')
    if description:
        parts.append(f'error_description="{description}"')
    return "Bearer " + ", ".join(parts)


# -- RFC 6749 helpers ------------------------------------------------------------

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _oauth_json(body: dict[str, Any], status: int = 200, headers: dict | None = None):
    return JSONResponse(body, status_code=status, headers={**_NO_STORE, **(headers or {})})


def _oauth_error(
    error: str,
    description: str | None = None,
    status: int = 400,
    headers: dict | None = None,
) -> JSONResponse:
    """An RFC 6749 section 5.2 error body."""
    body: dict[str, Any] = {"error": error}
    if description:
        body["error_description"] = description
    return _oauth_json(body, status, headers)


async def _oauth_fields(request: Request) -> dict[str, str] | None:
    """Request fields from a form-encoded body (or, as a courtesy, JSON).

    ``None`` means the body is malformed. A repeated form field is malformed
    too (RFC 6749 section 3.1).
    """
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    raw = await request.body()
    if ctype == "application/json":
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        out: dict[str, str] = {}
        for k, v in data.items():
            if v is None:
                continue
            if not isinstance(v, str):
                return None
            out[str(k)] = v
        return out
    try:
        parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True, strict_parsing=False)
    except UnicodeDecodeError:
        return None
    if any(len(v) > 1 for v in parsed.values()):
        return None
    return {k: v[0] for k, v in parsed.items()}


def _page(
    title: str, body: str, status: int = 200, headers: dict[str, str] | None = None
) -> HTMLResponse:
    """A small server-rendered page. ``body`` must already be escaped."""
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;"
        "padding:0 1rem;line-height:1.5}label{display:block;margin:.6rem 0}"
        "label small{display:block;color:#555;margin-left:1.6rem}"
        "label ul{margin:.2rem 0 0 1.6rem;padding-left:1rem;color:#555;font-size:.9em}"
        "button{font-size:1rem;padding:.5rem 1.2rem;margin-right:.5rem}"
        ".warn{border-left:4px solid #b45309;background:#fef3c7;color:#1f2937;"
        "padding:.5rem .8rem}</style></head>"
        f"<body><h1>{escape(title)}</h1>{body}</body></html>"
    )
    # A consent page must never be framed: a hidden frame could click Allow.
    return HTMLResponse(
        html,
        status_code=status,
        headers={
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "frame-ancestors 'none'",
            "Cache-Control": "no-store",
            **(headers or {}),
        },
    )


def _back(redirect_uri: str, iss: str, **params: str) -> RedirectResponse:
    """An authorization response, always naming its issuer (RFC 9207)."""
    sep = "&" if "?" in redirect_uri else "?"
    query = urlencode({**{k: v for k, v in params.items() if v}, "iss": iss})
    return RedirectResponse(redirect_uri + sep + query, status_code=302)


def _s256(verifier: str) -> str:
    """The S256 challenge for a verifier that already matched ``_VERIFIER_RE``."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# -- metadata-document fetch budget ---------------------------------------------


class _FetchBudget:
    """Fixed-window counters for metadata-document fetches (cache misses only).

    Every miss makes the server fetch a URL the caller chose, so it is limited
    per client IP and per target host. In-process, like snapgate's other limits.
    """

    def __init__(self, limit: int = 10, window: float = 600.0):
        self.limit = limit
        self.window = window
        self._lock = threading.Lock()
        self._counts: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        if not settings.RATE_LIMIT_ENABLED:
            return True
        now = time.monotonic()
        with self._lock:
            start, count = self._counts.get(key, (now, 0))
            if now - start >= self.window:
                start, count = now, 0
            if count >= self.limit:
                return False
            self._counts[key] = (start, count + 1)
            return True

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


_fetch_budget = _FetchBudget()


def _over_limit(request: Request, name: str) -> JSONResponse | None:
    """An RFC 6749 429 if this request is over snapgate's limit ``name``."""
    if within_rate_limit(request, name):
        return None
    return _oauth_error("temporarily_unavailable", "too many requests; retry later", status=429)


# -- the asking app --------------------------------------------------------------


@dataclass(frozen=True)
class _App:
    """The asking app, whichever way it identified itself."""

    client_id: str
    name: str
    redirect_uris: tuple[str, ...]
    #: The client_id URL's host for a metadata-document app; None for a registered one.
    host: str | None
    #: The app's own command-line tool (``OAuthServerConfig.native_client``).
    native: bool = False

    def redirect_ok(self, uri: str) -> bool:
        """A listed redirect URI; for the native client, any loopback callback."""
        if self.native:
            return native_redirect_ok(uri)
        return uri in self.redirect_uris


_LOOPBACK = ("localhost", "127.0.0.1", "::1")

_FIELDS = (
    "client_id",
    "redirect_uri",
    "response_type",
    "state",
    "code_challenge",
    "code_challenge_method",
    "resource",
    "scope",
)


def _consent_user(request: Request, db: Session, config: OAuthServerConfig) -> User | None:
    """The person signed in for the consent page, of any status, or None.

    Only this module reads the consent sign-in; it is never an API credential.
    """
    marker = request.session.get(CONSENT_USER_KEY)
    if not isinstance(marker, dict):
        return None
    user_id, at = marker.get("id"), marker.get("at")
    if not isinstance(user_id, int) or not isinstance(at, int | float):
        return None
    if time.time() - at > config.consent_login_ttl.total_seconds():
        return None
    return db.get(User, user_id)


def _sign_in(config: OAuthServerConfig, authorize_url: str, select_account: bool = False) -> str:
    """The sign-in URL that returns to ``authorize_url`` (a consent-page path).

    The app's ``config.sign_in`` hook if set (relative or absolute; app
    configuration, so not validated), else snapgate's own sign-in.
    """
    if config.sign_in is not None:
        return config.sign_in(authorize_url, select_account)
    target = "/auth/login?next=" + quote(authorize_url, safe="")
    if select_account:
        target += "&prompt=select_account"
    return target


def create_oauth_router(get_db: Callable, config: OAuthServerConfig) -> APIRouter:
    """Build the OAuth authorization server routes for one app.

    Routes (no prefix): the two discovery documents, ``/oauth/register``,
    ``/oauth/authorize`` (GET page, POST decision), ``/oauth/token``,
    ``/oauth/revoke``, and the JWT-only ``/auth/connections`` list / change /
    revoke.

    Installs ``config`` module-wide (one OAuth config per process; the last
    call wins).
    """
    global _installed, _metadata_cache
    _installed = config
    fetch = config.client_metadata_fetch or partial(fetch_client_metadata, app_name=config.app_name)
    _metadata_cache = ClientMetadataCache(fetch)
    cache = _metadata_cache
    scopes = config.scopes
    native = config.native_client
    key_prefix = config.access_key_prefix or settings.API_KEY_PREFIX
    _, require_user, _ = create_auth_dependencies(get_db)

    # Handlers that touch the DB are plain ``def``, or async only to read the
    # body and then ``run_in_threadpool``: SQLAlchemy sessions and bcrypt block.
    router = APIRouter(tags=["OAuth"])

    def issuer(request: Request) -> str:
        return _issuer(config, request)

    def resource_url(request: Request) -> str:
        return issuer(request) + config.resource_path

    def native_resource_url(request: Request) -> str:
        return issuer(request) + native.resource_path

    def resource_ok(request: Request, resource: str, client_id: str = "") -> bool:
        """RFC 8707: an absent ``resource`` is fine; a present one must be ours.

        The native client's resource is its own ``resource_path``.
        """
        if not resource:
            return True
        wanted = (
            native_resource_url(request) if config.is_native(client_id) else resource_url(request)
        )
        return resource.rstrip("/") == wanted.rstrip("/")

    resource_hint = f"resource must be this server's {config.resource_path}"
    native_resource_hint = (
        f"resource must be this server's {native.resource_path}" if native else ""
    )
    native_scope_hint = "scope must be " + " ".join(native.scopes) if native else ""
    scope_hint = "unknown scope; use " + ", ".join(sorted(scopes.names))

    # -- discovery ---------------------------------------------------------------

    def protected_resource(request: Request):
        """RFC 9728: the resource is protected by this origin's authorization server."""
        return {
            "resource": resource_url(request),
            "authorization_servers": [issuer(request)],
            "bearer_methods_supported": ["header"],
            "scopes_supported": sorted(scopes.names),
        }

    router.add_api_route(PROTECTED_RESOURCE_PATH, protected_resource, methods=["GET"])
    router.add_api_route(
        PROTECTED_RESOURCE_PATH + config.resource_path, protected_resource, methods=["GET"]
    )

    @router.get("/.well-known/oauth-authorization-server")
    def authorization_server(request: Request):
        """RFC 8414: where to register, authorize and trade a code."""
        origin = issuer(request)
        return {
            "issuer": origin,
            "authorization_endpoint": origin + AUTHORIZE_PATH,
            "token_endpoint": origin + TOKEN_PATH,
            "registration_endpoint": origin + REGISTER_PATH,
            "revocation_endpoint": origin + REVOKE_PATH,
            "revocation_endpoint_auth_methods_supported": ["none"],
            "response_types_supported": ["code"],
            "grant_types_supported": GRANT_TYPES,
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "client_id_metadata_document_supported": True,
            "authorization_response_iss_parameter_supported": True,
            "scopes_supported": sorted(scopes.names),
        }

    # -- registration ------------------------------------------------------------

    @router.post(REGISTER_PATH, status_code=201)
    async def register(request: Request, db: Session = Depends(get_db)):
        """RFC 7591 dynamic client registration. Public clients only; no secret.

        Only the body read is async; the limit check and DB work run in the threadpool.
        """
        try:
            body = await request.json()
        except ValueError:
            body = None
        return await run_in_threadpool(register_sync, request, db, body)

    def register_sync(request: Request, db: Session, body: Any):
        refused = _over_limit(request, "oauth_register")
        if refused is not None:
            return refused
        if not isinstance(body, dict):
            return _oauth_error("invalid_client_metadata", "send a JSON object")
        uris = body.get("redirect_uris")
        if (
            not isinstance(uris, list)
            or not uris
            or len(uris) > 10
            or not all(isinstance(u, str) and redirect_uri_ok(u) for u in uris)
        ):
            return _oauth_error(
                "invalid_redirect_uri",
                "redirect_uris must be https URLs (http only for localhost), without a fragment",
            )
        name = body.get("client_name")
        name = (name.strip() if isinstance(name, str) else "")[:80] or "An AI app"
        client = OAuthClient(
            client_id=CLIENT_PREFIX + secrets.token_urlsafe(16),
            client_name=name,
            redirect_uris=uris,
        )
        db.add(client)
        db.commit()
        db.refresh(client)
        return _oauth_json(
            {
                "client_id": client.client_id,
                "client_name": client.client_name,
                "redirect_uris": client.redirect_uris,
                "client_id_issued_at": int(_aware(client.created_at).timestamp()),
                "grant_types": GRANT_TYPES,
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
            status=201,
        )

    # -- the browser side --------------------------------------------------------

    def resolve_client(request: Request, db: Session, client_id: str):
        """``(app, None)`` for a known app, else ``(None, page)``. Never redirects."""
        if config.is_native(client_id):
            return _App(native.client_id, native.name, (), None, native=True), None
        if is_metadata_client_id(client_id):
            try:
                host = check_client_id_url(client_id)
            except ClientMetadataError as e:
                return None, _page("Unknown app", f"<p>{escape(str(e))}.</p>", 400)
            if cache.peek(client_id) is None and not (
                _fetch_budget.allow("ip:" + client_ip(request))
                and _fetch_budget.allow("host:" + host)
            ):
                return None, _page(
                    "Too many sign-in attempts", "<p>Try again in a few minutes.</p>", 429
                )
            try:
                md = cache.get(client_id)
            except ClientMetadataError as e:
                return None, _page(
                    "Unknown app",
                    f"<p>{escape(config.app_name)} could not read this app's description at "
                    f"<strong>{escape(host)}</strong>: {escape(str(e))}.</p>",
                    400,
                )
            return _App(md.client_id, md.client_name, md.redirect_uris, md.host), None
        row = None
        if client_id:
            row = db.exec(select(OAuthClient).where(OAuthClient.client_id == client_id)).first()
        if row is None:
            return None, _page(
                "Unknown app",
                f"<p>This app is not registered with {escape(config.app_name)}.</p>",
                400,
            )
        return _App(row.client_id, row.client_name, tuple(row.redirect_uris), None), None

    def check_request(request: Request, db: Session, params: dict[str, str]):
        """``(app, requested, None)`` for a good request, else ``(None, None, response)``.

        An unknown client, an unusable metadata document, or an unlisted redirect
        URI is shown as a page, never redirected to (that would be an open
        redirect). Every later problem goes back to the app as an OAuth error.
        """
        app, refusal = resolve_client(request, db, params.get("client_id", ""))
        if refusal is not None:
            return None, None, refusal
        redirect_uri = params.get("redirect_uri", "")
        if not app.redirect_ok(redirect_uri):
            return (
                None,
                None,
                _page(
                    "Unknown return address",
                    "<p>This app asked to return somewhere it did not register.</p>",
                    400,
                ),
            )
        state = params.get("state", "")

        def back(error: str, description: str | None = None):
            return (
                None,
                None,
                _back(
                    redirect_uri,
                    issuer(request),
                    error=error,
                    error_description=description or "",
                    state=state,
                ),
            )

        if params.get("response_type") != "code":
            return back("unsupported_response_type")
        if not params.get("code_challenge") or params.get("code_challenge_method") != "S256":
            return back("invalid_request", "PKCE with code_challenge_method=S256 is required")
        if not _CHALLENGE_RE.fullmatch(params["code_challenge"]):
            return back(
                "invalid_request", "code_challenge must be an S256 value (43 base64url characters)"
            )
        if app.native:
            # Its own resource, named exactly; its own scopes, exactly or not at all.
            resource = params.get("resource", "").rstrip("/")
            if resource != native_resource_url(request).rstrip("/"):
                return back("invalid_target", native_resource_hint)
            asked = params.get("scope", "").split()
            if asked and set(asked) != set(native.scopes):
                return back("invalid_scope", native_scope_hint)
            return app, frozenset(native.scopes), None
        if not resource_ok(request, params.get("resource", "")):
            return back("invalid_target", resource_hint)
        requested = scopes.parse_request(params.get("scope", ""))
        if requested is None:
            return back("invalid_scope", scope_hint)
        return app, requested, None

    def sign_in_redirect(params: dict[str, str]) -> RedirectResponse:
        query = urlencode({k: v for k, v in params.items() if v})
        return RedirectResponse(_sign_in(config, f"{AUTHORIZE_PATH}?{query}"), status_code=302)

    def not_approved_page() -> HTMLResponse:
        return _page(
            "Your account is waiting for approval",
            f"<p>An administrator of {escape(config.app_name)} has to approve your account "
            "before you can connect an app to it.</p>",
            403,
        )

    def group_html(group, offered) -> str:
        if group.description:
            detail = f"<small>{escape(group.description)}</small>"
        else:
            items = "".join(
                f"<li>{escape(scopes.scopes[n])}</li>" for n in sorted(scopes.expand(group.scopes))
            )
            detail = f"<ul>{items}</ul>"
        included = [g.label for g in scopes.includes(group) if g in offered]
        if included:
            detail += f"<small>Includes: {escape(', '.join(included))}</small>"
        return f"<strong>{escape(group.label)}</strong>{detail}"

    @router.get(AUTHORIZE_PATH)
    def authorize(request: Request, db: Session = Depends(get_db)):
        """The pop-up: sign in if needed, then ask what the app may do.

        A plain ``def``: FastAPI runs it in the threadpool, so a metadata-document
        fetch here never blocks the event loop.
        """
        params = {k: request.query_params.get(k, "") for k in _FIELDS}
        app, requested, refusal = check_request(request, db, params)
        if refusal is not None:
            return refusal
        user = _consent_user(request, db, config)
        if user is None:
            return sign_in_redirect(params)
        if user.status != UserStatus.APPROVED:
            return not_approved_page()

        nonce = secrets.token_urlsafe(24)
        request.session[CSRF_KEY] = nonce
        name = escape(app.name)
        app_name = escape(config.app_name)
        hidden = "".join(
            f"<input type='hidden' name='{k}' value='{escape(params[k])}'>" for k in _FIELDS
        )
        hidden += f"<input type='hidden' name='csrf' value='{escape(nonce)}'>"
        this_url = AUTHORIZE_PATH + "?" + request.url.query
        other = escape(_sign_in(config, this_url, select_account=True))
        signed_in_as = (
            f"<p>Signed in as {escape(user.email)}. Not you? "
            f"<a href='{other}'>Use another account</a></p>"
        )
        buttons = (
            "<button type='submit' name='decision' value='approve'>Allow</button>"
            "<button type='submit' name='decision' value='deny'>Deny</button></form>"
        )
        if app.native:
            return _page(
                "Sign in to the command line?",
                f"<p><strong>{name}</strong> on this computer wants to act as you in "
                f"{app_name}.</p>"
                f"<p>{escape(native.consent_text)}</p>"
                "<p class='warn'>This app runs on your own computer. Only continue if "
                f"you just started signing in to {name} yourself.</p>"
                f"<p>It shows up on {escape(config.connections_location)}, where you can "
                "revoke it.</p>"
                f"<form method='post' action='{AUTHORIZE_PATH}'>{hidden}"
                f"{signed_in_as}{buttons}",
            )
        offered = scopes.offered_groups(requested)
        # One field per group, not a repeated field: _oauth_fields refuses those.
        options = "".join(
            f"<label><input type='checkbox' name='allow_{escape(g.id)}' value='on' checked> "
            f"{group_html(g, offered)}</label>"
            for g in offered
        )
        redirect_host = (urlsplit(params["redirect_uri"]).hostname or "").lower()
        who = f"<p><strong>{name}</strong> wants to use your {app_name} account.</p>"
        if app.host is not None:
            host = escape(app.host)
            who += (
                f"<p class='warn'>This app is described by <strong>{host}</strong>. "
                f"Only continue if you trust {host}.</p>"
            )
        who += (
            "<p>After you choose, you will be sent back to "
            f"<strong>{escape(redirect_host)}</strong>.</p>"
        )
        if redirect_host in _LOOPBACK:
            who += (
                "<p class='warn'>This app runs on your own computer. Only continue if "
                f"you just started connecting {name} yourself.</p>"
            )
        note = f"<p>{escape(config.connection_note)}</p>" if config.connection_note else ""
        return _page(
            f"Connect {app.name}?",
            f"{who}<form method='post' action='{AUTHORIZE_PATH}'>{hidden}"
            f"<fieldset><legend>What do you allow it to do?</legend>{options}</fieldset>{note}"
            f"<p>This connection works only through {app_name}'s AI connector. It shows "
            f"up on {escape(config.connections_location)}, where you can change what it "
            "may do or revoke it.</p>"
            f"{signed_in_as}{buttons}",
        )

    @router.post(AUTHORIZE_PATH)
    async def authorize_decision(request: Request, db: Session = Depends(get_db)):
        """The person's answer. Allow stores a single-use code; both go back to the app.

        Only the body read is async: the metadata fetch, DB work and session
        changes run in the threadpool (``request.session`` is a plain dict).
        """
        fields = await _oauth_fields(request)
        return await run_in_threadpool(decide, request, db, fields)

    def choose(fields: dict[str, str], requested: frozenset[str]):
        """What the ticked ``allow_<id>`` boxes grant, or a refusal page."""
        chosen = [
            k.removeprefix("allow_")
            for k, v in fields.items()
            if k.startswith("allow_") and v == "on"
        ]
        offered_ids = {g.id for g in scopes.offered_groups(requested)}
        if any(gid not in offered_ids for gid in chosen):
            # A hand-edited form cannot grant more than the app asked for.
            return _page(
                "Not allowed",
                "<p>The app did not ask for that much access. Close this window and try again.</p>",
                400,
            )
        if not chosen:
            return _page(
                "Choose at least one thing the app may do",
                "<p>Choose at least one thing the app may do.</p>",
                400,
            )
        granted = scopes.grant(requested, chosen)
        if granted is None:
            return _page(
                "Not allowed",
                "<p>The app did not ask for that much access. Close this window and try again.</p>",
                400,
            )
        return granted

    def decide(request: Request, db: Session, fields: dict[str, str] | None):
        if fields is None:
            return _page("Something went wrong", "<p>The form could not be read.</p>", 400)
        params = {k: fields.get(k, "") for k in _FIELDS}
        app, requested, refusal = check_request(request, db, params)
        if refusal is not None:
            return refusal
        user = _consent_user(request, db, config)
        if user is None:
            return _page(
                "Please sign in again",
                "<p>Your sign-in for this page has expired. Close this window and connect "
                "the app again.</p>",
                401,
            )
        if user.status != UserStatus.APPROVED:
            return not_approved_page()
        expected = request.session.pop(CSRF_KEY, None)
        presented = fields.get("csrf", "")
        if not (
            isinstance(expected, str)
            and presented
            and secrets.compare_digest(presented.encode(), expected.encode())
        ):
            return _page(
                "Something went wrong",
                "<p>This form is out of date. Close this window and connect the app again.</p>",
                400,
            )
        # One sign-in backs one decision.
        request.session.pop(CONSENT_USER_KEY, None)
        if fields.get("decision") != "approve":
            return _back(
                params["redirect_uri"],
                issuer(request),
                error="access_denied",
                state=params["state"],
            )
        if app.native:
            granted = frozenset(native.scopes)  # fixed; any allow_* field is ignored
        else:
            granted = choose(fields, requested)
            if isinstance(granted, HTMLResponse):
                return granted

        code = CODE_PREFIX + secrets.token_urlsafe(32)
        now = _now()
        # Spent codes stay for the reuse window, so a replay can still revoke.
        db.exec(
            delete(OAuthCode).where(col(OAuthCode.expires_at) <= now - config.code_reuse_window),
            execution_options={"synchronize_session": False},
        )
        db.add(
            OAuthCode(
                code_hash=hash_token(code),
                client_id=app.client_id,
                client_name=app.name,
                client_host=app.host,
                user_id=user.id,
                scopes=sorted(granted),
                redirect_uri=params["redirect_uri"],
                code_challenge=params["code_challenge"],
                expires_at=now + config.code_ttl,
            )
        )
        db.commit()
        return _back(params["redirect_uri"], issuer(request), code=code, state=params["state"])

    # -- the app side ------------------------------------------------------------

    def token_response(access_token: str, refresh_token: str, held: Iterable[str]):
        return _oauth_json(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": int(config.access_ttl.total_seconds()),
                "refresh_token": refresh_token,
                "scope": scopes.scope_string(held),
            }
        )

    def held_scopes(conn_or_code) -> frozenset[str]:
        """A connection's (or code's) scopes: the native client's as stored."""
        if config.is_native(conn_or_code.client_id):
            return frozenset(conn_or_code.scopes)
        return scopes.expand(conn_or_code.scopes)

    def idle_ttl(client_id: str) -> timedelta:
        return native.refresh_idle_ttl if config.is_native(client_id) else config.refresh_idle_ttl

    def mint_key(db: Session, conn: OAuthConnection, now: datetime) -> str:
        is_native = config.is_native(conn.client_id)
        _, access_token = create_api_key(
            db,
            conn.user_id,
            f"{conn.client_name} login" if is_native else f"{conn.client_name} connector",
            scopes=sorted(conn.scopes),
            confined_to=native.resource_path if is_native else config.resource_path,
            connection_id=conn.id,
            expires_at=now + config.access_ttl,
            key_prefix=key_prefix,
            commit=False,
        )
        return access_token

    def revoke(db: Session, conn: OAuthConnection, now: datetime) -> None:
        """Revoke a connection and its keys (caller commits)."""
        conn.revoked_at = now
        db.add(conn)
        revoke_connection_keys(db, conn.id, commit=False)

    def revoke_by_id(db: Session, conn_id: int, now: datetime) -> None:
        """Revoke a connection under its row lock, then commit (a replay was seen)."""
        conn = db.exec(
            select(OAuthConnection)
            .where(OAuthConnection.id == conn_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if conn is not None and conn.revoked_at is None:
            revoke(db, conn, now)
        db.commit()

    @router.post(TOKEN_PATH)
    async def token(request: Request, db: Session = Depends(get_db)):
        """Trade a code (with its PKCE verifier) or a refresh token for a new pair.

        Only the body read is async; the limit check, DB work and bcrypt run in
        the threadpool.
        """
        fields = await _oauth_fields(request)
        return await run_in_threadpool(token_sync, request, db, fields)

    def token_sync(request: Request, db: Session, fields: dict[str, str] | None):
        refused = _over_limit(request, "oauth_token")
        if refused is not None:
            return refused
        if fields is None:
            return _oauth_error("invalid_request", "unreadable request body")
        grant_type = fields.get("grant_type")
        if grant_type not in GRANT_TYPES:
            return _oauth_error("unsupported_grant_type", "use authorization_code or refresh_token")
        client_id = fields.get("client_id", "")
        if not resource_ok(request, fields.get("resource", ""), client_id):
            hint = native_resource_hint if config.is_native(client_id) else resource_hint
            return _oauth_error("invalid_target", hint)
        if grant_type == "refresh_token":
            return refresh(db, fields)
        return redeem_code(db, fields)

    def redeem_code(db: Session, fields: dict[str, str]):
        """The first token pair. Each code works once; a reused code revokes its connection."""
        code = fields.get("code", "")
        verifier = fields.get("code_verifier", "")
        if not code or not verifier:
            return _oauth_error("invalid_request", "code and code_verifier are required")
        if not _VERIFIER_RE.fullmatch(verifier):
            return _oauth_error(
                "invalid_request", "code_verifier must be 43-128 characters of A-Z a-z 0-9 - . _ ~"
            )
        invalid = _oauth_error("invalid_grant", "unknown, expired, used or mismatched code")
        now = _now()
        code_hash = hash_token(code)
        # Spent on the first presentation, right or wrong, so a code cannot be
        # guessed at with many verifiers. A conditional UPDATE: only one caller
        # wins. Not committed yet: the spend and the new connection commit
        # together, so a concurrent presentation waits on this row (Postgres
        # row lock, SQLite write lock) and then sees which connection it minted.
        spent = db.exec(
            update(OAuthCode)
            .where(col(OAuthCode.code_hash) == code_hash, col(OAuthCode.used_at).is_(None))
            .values(used_at=now),
            execution_options={"synchronize_session": False},
        )
        if (spent.rowcount or 0) != 1:
            db.rollback()
            code_replayed(db, code_hash, now)
            return invalid
        row = db.exec(select(OAuthCode).where(OAuthCode.code_hash == code_hash)).one()
        user = db.get(User, row.user_id)
        if (
            _aware(row.expires_at) <= now
            or row.client_id != fields.get("client_id")
            or row.redirect_uri != fields.get("redirect_uri")
            or not secrets.compare_digest(_s256(verifier), row.code_challenge)
            or user is None
            or user.status != UserStatus.APPROVED
        ):
            db.commit()  # the spend sticks even though the trade failed
            return invalid

        granted = held_scopes(row)
        refresh_token = REFRESH_PREFIX + secrets.token_urlsafe(32)
        conn = OAuthConnection(
            user_id=row.user_id,
            client_id=row.client_id,
            client_name=row.client_name,
            client_host=row.client_host,
            scopes=sorted(granted),
            refresh_hash=hash_token(refresh_token),
            expires_at=now + idle_ttl(row.client_id),
        )
        db.add(conn)
        db.flush()
        access_token = mint_key(db, conn, now)
        # Step-up: the same app's live connections this one strictly widens.
        # Not for the native client: each computer's login is its own.
        older = (
            []
            if config.is_native(row.client_id)
            else db.exec(
                select(OAuthConnection).where(
                    OAuthConnection.user_id == row.user_id,
                    OAuthConnection.client_id == row.client_id,
                    col(OAuthConnection.revoked_at).is_(None),
                    OAuthConnection.id != conn.id,
                )
            ).all()
        )
        for other in older:
            if scopes.expand(other.scopes) < granted:
                revoke(db, other, now)
        row.connection_id = conn.id
        db.add(row)
        db.commit()
        return token_response(access_token, refresh_token, granted)

    def code_replayed(db: Session, code_hash: str, now: datetime) -> None:
        """A spent code came back: revoke the connection it minted, if any.

        Whichever ``client_id`` is presented: the code itself leaked. A code
        spent by a failed trade minted nothing, so its reuse revokes nothing.
        """
        row = db.exec(select(OAuthCode).where(OAuthCode.code_hash == code_hash)).first()
        if row is None or row.used_at is None or row.connection_id is None:
            db.rollback()
            return
        revoke_by_id(db, row.connection_id, now)

    def refresh(db: Session, fields: dict[str, str]):
        """A new access key under the same connection, and the next refresh token.

        The previous access key is left to run out: revoking it would break
        requests already in flight.
        """
        presented = fields.get("refresh_token", "")
        if not presented:
            return _oauth_error("invalid_request", "refresh_token is required")
        invalid = _oauth_error(
            "invalid_grant", "unknown, expired, revoked or mismatched refresh token"
        )
        now = _now()
        presented_hash = hash_token(presented)
        conn = db.exec(
            select(OAuthConnection).where(OAuthConnection.refresh_hash == presented_hash)
        ).first()
        if conn is None:
            past = db.exec(
                select(OAuthRefreshHistory).where(OAuthRefreshHistory.token_hash == presented_hash)
            ).first()
            if past is None:
                return invalid  # never issued, or older than refresh_history_keep
            if _aware(past.grace_until) <= now:
                # A retired token came back after its window: someone else
                # holds a copy. End the connection for everyone.
                revoke_by_id(db, past.connection_id, now)
                return invalid
            conn = db.get(OAuthConnection, past.connection_id)
            if conn is None:
                return invalid
        user = db.get(User, conn.user_id)
        if (
            conn.revoked_at is not None
            or _aware(conn.expires_at) <= now
            or conn.client_id != fields.get("client_id")
            or user is None
            or user.status != UserStatus.APPROVED
        ):
            return invalid
        held = held_scopes(conn)
        raw_scope = fields.get("scope", "")
        if raw_scope.strip() and config.is_native(conn.client_id):
            if set(raw_scope.split()) != held:
                return _oauth_error("invalid_scope", native_scope_hint)
        elif raw_scope.strip():
            asked = scopes.parse_request(raw_scope)
            if asked is None:
                return _oauth_error("invalid_scope", scope_hint)
            if not asked <= held:
                return _oauth_error(
                    "invalid_scope",
                    "a refresh cannot widen a connection; sign in again to ask for more",
                )
            if asked != held:
                return _oauth_error(
                    "invalid_scope",
                    f"change a connection's access on {config.connections_location}",
                )

        # Rotate under a row lock. The token being replaced goes into the
        # history table with a grace window. When the presented token is the
        # current one, that window is fresh. When it is a retired one still in
        # its window (an overlapping refresh), the displaced current token
        # inherits that window: windows never extend. A client that saved
        # either response keeps working inside the window and is revoked after.
        locked = db.exec(
            select(OAuthConnection)
            .where(OAuthConnection.id == conn.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if locked is None or locked.revoked_at is not None:
            db.rollback()
            return invalid
        if locked.refresh_hash == presented_hash:
            grace_until = now + config.refresh_grace
        else:
            past = db.exec(
                select(OAuthRefreshHistory).where(
                    OAuthRefreshHistory.token_hash == presented_hash,
                    OAuthRefreshHistory.connection_id == locked.id,
                )
            ).first()
            if past is None or _aware(past.grace_until) <= now:
                db.rollback()
                return invalid
            grace_until = _aware(past.grace_until)
        db.add(
            OAuthRefreshHistory(
                connection_id=locked.id,
                token_hash=locked.refresh_hash,
                retired_at=now,
                grace_until=grace_until,
            )
        )
        refresh_token = REFRESH_PREFIX + secrets.token_urlsafe(32)
        locked.refresh_hash = hash_token(refresh_token)
        locked.last_refreshed_at = now
        locked.expires_at = now + idle_ttl(locked.client_id)
        db.add(locked)
        access_token = mint_key(db, locked, now)
        db.exec(
            delete(OAuthRefreshHistory).where(
                col(OAuthRefreshHistory.connection_id) == locked.id,
                col(OAuthRefreshHistory.retired_at) < now - config.refresh_history_keep,
            ),
            execution_options={"synchronize_session": False},
        )
        db.commit()
        prune_connection_keys(db, locked.id, now - config.access_key_keep)
        return token_response(access_token, refresh_token, held_scopes(locked))

    @router.post(REVOKE_PATH)
    async def revoke_token(request: Request, db: Session = Depends(get_db)):
        """RFC 7009: end the connection a refresh token belongs to.

        Only the body read is async; the rest runs in the threadpool.
        """
        fields = await _oauth_fields(request)
        return await run_in_threadpool(revoke_sync, request, db, fields)

    def revoke_sync(request: Request, db: Session, fields: dict[str, str] | None):
        """Always 200 ``{}`` (RFC 7009 section 2.2), whether or not anything was
        revoked: an unknown, spent or foreign token reveals nothing. A token of
        another client_id revokes nothing. Access keys are not accepted here;
        they lapse within ``access_ttl`` once their connection is revoked
        (revoking the connection revokes them at once)."""
        if not within_rate_limit(request, "oauth_token", "oauth_revoke"):
            return _oauth_error("temporarily_unavailable", "too many requests; retry later", 429)
        if fields is None:
            return _oauth_error("invalid_request", "unreadable request body")
        presented = fields.get("token", "")
        if presented.startswith(REFRESH_PREFIX):
            presented_hash = hash_token(presented)
            conn = db.exec(
                select(OAuthConnection).where(OAuthConnection.refresh_hash == presented_hash)
            ).first()
            if conn is None:
                past = db.exec(
                    select(OAuthRefreshHistory).where(
                        OAuthRefreshHistory.token_hash == presented_hash
                    )
                ).first()
                conn = db.get(OAuthConnection, past.connection_id) if past else None
            if (
                conn is not None
                and conn.revoked_at is None
                and conn.client_id == fields.get("client_id")
            ):
                revoke_by_id(db, conn.id, _now())
        return _oauth_json({})

    # -- connection management (JWT only) ----------------------------------------

    def live(conn: OAuthConnection | None, user: User, now: datetime) -> bool:
        return (
            conn is not None
            and conn.user_id == user.id
            and conn.revoked_at is None
            and _aware(conn.expires_at) > now
        )

    def connection_info(conn: OAuthConnection) -> dict[str, Any]:
        fixed = config.is_native(conn.client_id)
        return {
            "id": conn.id,
            "client_name": conn.client_name,
            "client_host": conn.client_host,
            "fixed": fixed,
            "scopes": (
                [{"name": n, "description": native.consent_text} for n in sorted(conn.scopes)]
                if fixed
                else scopes.describe(conn.scopes)
            ),
            "groups": [] if fixed else scopes.groups_held(conn.scopes),
            "created_at": _aware(conn.created_at).isoformat(),
            "last_refreshed_at": (
                _aware(conn.last_refreshed_at).isoformat() if conn.last_refreshed_at else None
            ),
            "expires_at": _aware(conn.expires_at).isoformat(),
        }

    def not_found() -> AuthError:
        return AuthError(
            user_message="Connection not found", status_code=404, error_code="connection_not_found"
        )

    class UpdateConnectionRequest(BaseModel):
        groups: list[str] = Field(min_length=1)

    @router.get("/auth/connections")
    def list_connections(user: User = Depends(require_user), db: Session = Depends(get_db)):
        """The caller's live connections (JWT only)."""
        now = _now()
        rows = db.exec(
            select(OAuthConnection)
            .where(
                OAuthConnection.user_id == user.id,
                col(OAuthConnection.revoked_at).is_(None),
            )
            .order_by(col(OAuthConnection.id))
        ).all()
        return {"connections": [connection_info(c) for c in rows if live(c, user, now)]}

    @router.patch("/auth/connections/{connection_id}")
    def update_connection(
        connection_id: int,
        body: UpdateConnectionRequest,
        user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ):
        """Change what a connection may do: the permission groups it holds (JWT only).

        Widening is allowed: the signed-in owner is the authority, as on the
        consent page. The connection's live keys change at once.
        """
        conn = db.get(OAuthConnection, connection_id)
        if not live(conn, user, _now()):
            raise not_found()
        if config.is_native(conn.client_id):
            raise AuthError(
                user_message="This sign-in cannot be changed, only revoked",
                error_code="fixed_connection",
            )
        picked: set[str] = set()
        for gid in body.groups:
            group = scopes.group(gid)
            if group is None:
                raise AuthError(user_message="Unknown permission", error_code="invalid_group")
            picked |= set(group.scopes)
        new_scopes = sorted(scopes.expand(picked))
        conn.scopes = new_scopes
        db.add(conn)
        set_connection_key_scopes(db, conn.id, new_scopes, commit=False)
        db.commit()
        db.refresh(conn)
        return connection_info(conn)

    @router.delete("/auth/connections/{connection_id}")
    def revoke_connection(
        connection_id: int,
        user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ):
        """Revoke a connection and its keys (JWT only)."""
        conn = db.get(OAuthConnection, connection_id)
        if conn is None or conn.user_id != user.id:
            raise not_found()
        if conn.revoked_at is None:
            revoke(db, conn, _now())
            db.commit()
        return {"message": "Connection revoked"}

    return router


__all__ = [
    "CODE_REUSE_WINDOW",
    "REFRESH_HISTORY_KEEP",
    "NativeClient",
    "OAuthServerConfig",
    "native_redirect_ok",
    "create_oauth_router",
    "www_authenticate",
]
