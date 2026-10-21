# Snapgate Module

## ⚠️ DROP-IN MODULE - DO NOT MODIFY

This is a **read-only drop-in module**. When using snapgate in your project:

- **NEVER modify files in this folder** - Copy from source as-is
- All customization uses **hooks** (`SnapgateHooks`) and **environment variables**
- App-specific code goes **outside** snapgate (your routes, dependencies, etc.)
- To update: overwrite entire folder from source snapgate

**For AI assistants:** Treat `snapgate/` as a black box. Never edit files inside it.

---

Drop-in Google OAuth authentication for FastAPI.

## How Snapgate Uses Cookies (Important!)

**Snapgate does NOT use cookies for authentication.** All auth uses `Authorization: Bearer <token>` headers.

Cookies are used **only** during the OAuth flow:
- When user clicks "Sign in with Google", snapgate stores PKCE verifier and OAuth state in a session cookie
- After Google redirects back, snapgate reads the cookie to verify the OAuth response
- The session cookie is then cleared

**Your app must add SessionMiddleware** for OAuth to work, but this cookie:
- Is temporary (only lives during OAuth redirect)
- Contains no auth tokens
- Is not used for subsequent API requests

**One narrow exception: the OAuth consent page.** Apps that mount the optional
OAuth server (`create_oauth_router`, see below) render a consent page on the
server, which cannot see the SPA's `localStorage` tokens. Signing in from that
page (`/auth/google?next=/oauth/authorize...`, or the app's own login when it
sets `OAuthServerConfig.sign_in`) stores a short-lived, consent-only marker
(`sg_consent_user`, 30 minutes) in the same signed session cookie, plus a CSRF
nonce for the form. Only `snapgate.oauth` reads it, one
sign-in backs one Allow/Deny decision, and it is **never an API credential**:
`/auth/me`, `/auth/api-keys` and every app route ignore it.

```python
# Required for OAuth flow only - NOT for authentication
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SESSION_SECRET_KEY,
    https_only=True,  # Set True in production
    same_site="lax",
)
```

**Authentication flow:**
1. OAuth callback returns tokens in URL: `?token=xxx&refresh=yyy`
2. Frontend stores tokens in localStorage (or memory)
3. All API requests use `Authorization: Bearer <token>` header
4. Token refresh uses POST body, not cookies

## Frontend Integration

> **Use the reference implementation at `frontend/src/snapgate/`.** Do not
> write your own token refresh, StrictMode-safe code exchange, or error
> classification. See [`frontend/src/snapgate/README.md`](../../frontend/src/snapgate/README.md)
> for copy instructions and a description of what each file provides.

Your frontend is responsible for storing tokens, sending them with requests, and handling session expiry.

**On session expiry** (refresh fails), clear everything and redirect:
```typescript
function handleSessionExpiry() {
  clearTokens();                          // 1. Clear localStorage
  authStore.logout();                     // 2. Clear auth state (context/store)
  queryClient.clear();                    // 3. Clear data caches (React Query, etc.)
  navigate('/login?session=expired');     // 4. Redirect
}
```

**Code smell warning:** If you need `window.location.href` (full page reload) to properly log out, your auth state isn't centralized. In a well-architected app, auth lives in one place, and clearing it triggers proper re-renders everywhere.

## Quick Start

```python
from fastapi import FastAPI, Depends
from sqlmodel import Session, create_engine
from snapgate import create_snapgate_router, create_auth_dependencies, User, settings, limiter

# Database
engine = create_engine("sqlite:///./app.db")


def get_db():
    with Session(engine) as session:
        yield session


# App
app = FastAPI()
app.state.limiter = limiter

# Mount auth routes at /auth/*
app.include_router(create_snapgate_router(get_db=get_db))

# Protect your routes
get_current_user, require_user, require_approved_user = create_auth_dependencies(get_db)


@app.get("/api/data")
def protected(user: User = Depends(require_approved_user)):
    return {"hello": user.name}
```

## What You Get

**Endpoints** (mounted at `/auth`):
- `GET /auth/google` - Start OAuth flow
- `GET /auth/google/callback` - OAuth callback (automatic)
- `GET /auth/me` - Get current user (via Bearer token)
- `POST /auth/refresh` - Refresh tokens (via request body)
- `POST /auth/logout` - Logout
- `POST|GET /auth/api-keys`, `DELETE /auth/api-keys/{id}` - Settings-page API keys
- `GET /auth/scopes` - The app's scope vocabulary (public; empty without a `ScopeConfig`)
- With the optional OAuth server: `/.well-known/oauth-*`, `/oauth/register`,
  `/oauth/authorize`, `/oauth/token`, and `GET|PATCH|DELETE /auth/connections`

**Dependencies** for protecting routes:
- `require_user` - Any authenticated user
- `require_approved_user` - Authenticated + admin-approved
- `require_user_flexible` - Accepts JWT Bearer token OR API key in header

**Models** (SQLModel tables created automatically):
- `User` - Google account info + approval status
- `RefreshToken` - Token rotation tracking
- `ApiKey` - For headless/API access (carries `scopes`, `confined_to`, `connection_id`)
- `OAuthClient`, `OAuthCode`, `OAuthConnection` - The optional OAuth server's state.
  These tables exist in every app's schema, whether or not it mounts the server.

## Configuration

**All configuration is via environment variables.** Never edit files in this module.

Snapgate uses `pydantic-settings`, which reads env vars matching field names in `config.py`. Set them in your `.env` file or environment.

### Required Environment Variables

```bash
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=xxx
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback
JWT_SECRET_KEY=<32+ chars>
SESSION_SECRET_KEY=<32+ chars>
FRONTEND_URL=http://localhost:5173
DATABASE_URL=sqlite:///./app.db
```

### Optional Environment Variables

```bash
# Token expiration (defaults: 15 min access, 30 day refresh)
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=30

# Algorithm (default: HS256; use RS256 for distributed systems)
ACCESS_TOKEN_ALGORITHM=HS256
JWT_PRIVATE_KEY=<PEM key>    # Required if RS256
JWT_PUBLIC_KEY=<PEM key>     # Required if RS256

# CORS (default: uses FRONTEND_URL)
ALLOWED_ORIGINS=https://app.example.com,https://staging.example.com

# API key prefix (default: snapgate_)
API_KEY_PREFIX=authormark_

# Rate limiting (default: enabled, 10/min)
RATE_LIMIT_ENABLED=true
RATE_LIMIT_PER_MINUTE=10

# Header carrying the real client IP. Set ONLY when a trusted proxy
# overwrites it on every request (e.g. CF-Connecting-IP behind Cloudflare,
# X-Real-IP behind your own nginx). Unset = use the socket peer address.
RATE_LIMIT_CLIENT_IP_HEADER=CF-Connecting-IP

# Demo login endpoints (local development only)
DEMO_LOGIN=false

# Site admins for snapgate.useradmin (comma-separated, case-insensitive).
# Listed people are approved at every sign-in; only these lists grant
# site_role ("superadmin" > "admin"). Read on every call.
SNAPGATE_SUPERADMIN_EMAILS=you@example.org
SNAPGATE_ADMIN_EMAILS=
```

## Middleware Setup

Required middleware (order matters):

```python
from starlette.middleware.sessions import SessionMiddleware
from slowapi.middleware import SlowAPIMiddleware
from fastapi.middleware.cors import CORSMiddleware

app.state.limiter = limiter

app.add_middleware(SessionMiddleware, secret_key=settings.SESSION_SECRET_KEY)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=[settings.FRONTEND_URL], allow_credentials=True, ...)
```

## Exports

```python
from snapgate import (
    # Router factory
    create_snapgate_router,
    # Dependency factories
    create_auth_dependencies,  # Returns (get_current_user, require_user, require_approved_user)
    create_flexible_auth_dependencies,  # Returns (get_current_user_flexible, require_user_flexible,
    #          require_approved_user_flexible, get_request_api_key)
    # API key verification
    authenticate_api_key,  # Returns ApiKeyMatch(user, key) or None
    ApiKeyMatch,
    MatchedKey,  # id, name, scopes, confined_to, connection_id (never hash or prefix)
    # Scopes
    ScopeConfig,
    ScopeGroup,
    UnknownScopeError,
    create_scope_guard,  # Returns require_scope(*names)
    InsufficientScopeError,  # 403 insufficient_scope
    KeyConfinedError,  # 403 key_confined
    # OAuth server for MCP connectors (optional)
    OAuthServerConfig,
    NativeClient,  # the app's own command-line login (optional)
    create_oauth_router,
    www_authenticate,
    request_within,
    # Service layer (see "Service layer: Caller and typed errors")
    Caller,
    caller_for,  # Returns Caller(user, scopes, via) for a session or key
    ServiceError,  # base: status, code, detail, data
    NotFound,  # 404 not_found
    Forbidden,  # 403 forbidden
    Conflict,  # 409 conflict
    Invalid,  # 422 invalid_input
    InsufficientScope,  # 403 insufficient_scope
    RateLimited,  # 429 rate_limited
    install_service_errors,
    tool_error_text,
    mcp_scope_challenge,
    # Models
    User,
    RefreshToken,
    ApiKey,
    UserStatus,
    OAuthClient,
    OAuthCode,
    OAuthConnection,
    # Lifecycle hooks
    SnapgateHooks,
    SignInAttempt,  # what on_sign_in_attempt receives
    SiteRole,  # Literal["superadmin", "admin"]
    # Config
    settings,
    limiter,
)
```

## Scopes

Scopes are opaque strings on each API key. The app declares the vocabulary;
snapgate stores the names (expanded through `implies`), rejects undeclared
ones, and offers a guard.

```python
from snapgate import ScopeConfig, ScopeGroup, create_scope_guard

SCOPES = ScopeConfig(
    scopes={
        "deadlines:read": "See your deadlines and their status.",
        "deadlines:write": "Change status, notes and subscriptions, and add deadlines.",
    },
    implies={"deadlines:write": ["deadlines:read"]},
    groups=[  # consent checkboxes; every scope in exactly one (omit: one per scope)
        ScopeGroup("read", "Read", ("deadlines:read",)),
        ScopeGroup("edit", "Edit", ("deadlines:write",)),  # shows "Includes: Read"
    ],
    default_scopes=("deadlines:read", "deadlines:write"),  # Settings-page keys
)
get_user, require_user, require_approved_user, get_request_api_key = (
    create_flexible_auth_dependencies(get_db, api_key_prefix=PREFIX)
)
require_scope = create_scope_guard(SCOPES, get_request_api_key)
app.include_router(create_snapgate_router(get_db, api_key_prefix=PREFIX, scopes=SCOPES))


@app.post("/api/deadlines", dependencies=[Depends(require_scope("deadlines:write"))])
def add_deadline(user=Depends(require_approved_user)): ...
```

- Pass the **same** `get_request_api_key` your user dependencies use, so
  FastAPI's per-request cache verifies the key once.
- `require_scope("typo")` raises `UnknownScopeError` at import time.
- **JWT (browser) callers hold every scope.** Scopes restrict keys only.
- A key without the scope gets 403 `insufficient_scope` with a
  `WWW-Authenticate` header naming the missing scope.
- The guard is a plain function too: call it inline with a `MatchedKey` you
  already have, e.g. `require_scope("deadlines:write")(key)`.
- `POST /auth/api-keys` takes an optional `scopes` list: absent means
  `default_scopes`, an undeclared name or an empty list is 400 `invalid_scope`.
  Without a `ScopeConfig`, keys carry `[]` and a `scopes` list is refused.
- When adopting scopes, set existing keys' `scopes` to your `default_scopes`.

## OAuth server for MCP connectors

Optional. It lets chat apps (claude.ai, ChatGPT, Claude Code) connect to your
MCP endpoint given only its URL. Ported from Prosopia.

```python
from snapgate import OAuthServerConfig, create_oauth_router

app.include_router(
    create_snapgate_router(get_db, hooks=hooks, api_key_prefix=PREFIX, scopes=SCOPES)
)
app.include_router(
    create_oauth_router(
        get_db,
        OAuthServerConfig(
            app_name="Deadliner",
            scopes=SCOPES,
            resource_path="/mcp",
            issuer=settings.public_url,
            access_key_prefix=PREFIX,
            connections_location="your Deadliner Settings page",
        ),
    )
)
# ... mount /mcp, then the SPA last.
# MCP tools call service functions (see "Service layer"), never the app's own HTTP API.
```

The flow:

1. `/mcp` without a key answers 401 with `WWW-Authenticate:
   www_authenticate(request)`, which points at
   `/.well-known/oauth-protected-resource/mcp` (RFC 9728).
2. `/.well-known/oauth-authorization-server` (RFC 8414) lists the endpoints and
   scopes, and says Client ID Metadata Documents are supported.
3. The app identifies itself with an `https` URL client_id (a metadata
   document, fetched only from public addresses and cached) or registers at
   `POST /oauth/register` (RFC 7591, public clients, `sgo_` ids).
4. `GET /oauth/authorize` signs the person in if needed (consent-only, see
   "How Snapgate Uses Cookies"), then shows a consent page: the app's name, the
   website vouching for it, where the person will be sent back, and
   checkboxes for the permission groups that fit the requested `scope`, each
   ticked. The person may untick; at least one must stay ticked, and a group
   that implies another (through `implies`) says "Includes: ..." and grants
   both. A hand-edited form cannot grant more than was requested (400). Allow
   stores a 10-minute single-use
   code. `code_challenge` must be an S256 value (43 base64url characters).
   Every redirect back to the app (code or error) carries `iss` (RFC 9207),
   and discovery says so (`authorization_response_iss_parameter_supported`).
5. `POST /oauth/token` trades the code and its PKCE S256 verifier (43-128
   characters of `A-Z a-z 0-9 - . _ ~`) for a one-hour access token (an
   ordinary API key with the chosen scopes, confined to `resource_path`) and
   a refresh token (`sgr_`). A code presented again after it was spent
   revokes the connection it minted (RFC 6749 section 4.1.2). Codes are kept
   `code_reuse_window` (one day) past expiry to catch that.
6. Each refresh retires the refresh token into `oauth_refresh_history` (sha256
   only) with a 120 s grace window for overlapping calls. Any retired token
   presented after its window revokes the connection, however many rotations
   old it is. A token displaced by an overlapping refresh inherits the
   original window; windows never extend. Retired hashes are kept
   `refresh_history_keep` (90 days, never less than `refresh_idle_ttl`). A
   connection idle for 90 days lapses. The person lists, changes (`PATCH {"groups": ["read"]}`, takes
   effect on live keys at once) or revokes connections at `/auth/connections`
   (JWT only).

Rules for the host app:

- **Set `issuer` in production.** Without it the issuer comes from the
  request's `Host` header, which is for development only.
- **Include the router before any SPA catch-all**, and keep `SessionMiddleware`.
- **One OAuth config per process.** `create_oauth_router` installs its config
  module-wide so `www_authenticate` and `InsufficientScopeError` can find the
  issuer; the last call wins (so test suites can build a fresh app per test).
- **Connection keys are confined.** They work only under `resource_path`, with
  no exception. Anywhere else they get 403 `key_confined` (`KeyConfinedError`).
  That is a valid key in the wrong place: **do not count it toward
  failed-credential limits.** The `Host` header is never trusted.
- Use `www_authenticate(request, error="invalid_token")` for a 401 with a bad
  key, and nothing (no `error`) when no key was sent. `description` must be
  plain ASCII without quotes.
- Connection keys do not appear in `GET /auth/api-keys` and cannot be deleted
  there; manage them as connections.
- `cleanup_expired_codes(db)` also deletes authorization codes expired for
  over `code_reuse_window`, and connections revoked or lapsed for over a day,
  with their keys, codes and refresh-token history.
- The token, register and consent POST handlers read the body on the event
  loop, then run everything else (DB work, bcrypt) in the threadpool. Your
  `get_db` session is used by one thread at a time.

### Consent sign-in with your own login

By default the consent page signs people in with snapgate's Google login. If
your people sign in some other way (OIDC, school SSO, hexclave), set
`OAuthServerConfig.sign_in`. Snapgate keeps everything else: discovery,
registration, PKCE, codes, refresh rotation, confinement and connections.

```python
sign_in: Callable[[str, bool], str] | None = None
# (authorize_url, select_account) -> URL to send the person to
```

`authorize_url` is the consent-page path to return to (always under
`/oauth/authorize`); `select_account` is True for the page's "Use another
account" link. The URL you return may be relative or absolute.

Your login route checks `next`, signs the person in, then hands them to
snapgate:

```python
from snapgate import get_or_create_external_user, safe_consent_next, set_consent_user

config = OAuthServerConfig(
    ...,
    sign_in=lambda url, pick: (
        "/login/consent?"
        + urlencode({"next": url, **({"prompt": "select_account"} if pick else {})})
    ),
)


@app.get("/login/consent")
async def consent_login(request: Request, next: str, prompt: str | None = None):
    request.session["consent_next"] = safe_consent_next(next)  # 400 if not the consent page
    return await my_idp.authorize_redirect(request, CALLBACK_URL, prompt=prompt)


@app.get("/login/consent/callback")
async def consent_login_callback(request: Request, db: Session = Depends(get_db)):
    claims = (await my_idp.authorize_access_token(request))["userinfo"]  # verified here
    user = get_or_create_external_user(
        db,
        issuer=claims["iss"],
        subject=claims["sub"],
        email=claims["email"],
        name=claims.get("name", claims["email"]),
        picture=claims.get("picture"),
        email_verified=claims.get("email_verified", False),
    )
    set_consent_user(request, user)
    return RedirectResponse(request.session.pop("consent_next"), status_code=302)
```

- `get_or_create_external_user` keys the person by `issuer#subject`, stored in
  `User.google_id` (no schema change). A returning person gets their row back
  with `name` and `picture` refreshed; `email` and `status` never change. New
  rows get `status` (default `PENDING`; the consent page needs `APPROVED`).
- **Identity conflicts:** a new identity never claims an existing row by
  email. That is 409 `identity_conflict` ("Use your original sign-in method").
  An unverified email is 403 `email_unverified`.
- It fires no `SnapgateHooks`; your login is yours, so run your own code
  there (for example, link the row to your app's user table, Option 2 below).
- Call `set_consent_user` only after you have verified the person. It is the
  consent-only marker, never an API credential.
- App-specific limits on a connection (which org or courses it may touch)
  belong in your own tables keyed by `OAuthConnection.id`, checked at request
  time from the connection key's `connection_id`.

### Command-line login (native app)

Optional. Your own command-line tool can sign a person in with the same
server (RFC 8252: loopback redirect + PKCE), and get a refreshable key that
acts as them on your API.

```python
from datetime import timedelta
from snapgate import NativeClient, OAuthServerConfig

OAuthServerConfig(
    ...,
    native_client=NativeClient(
        client_id="myapp-cli",  # fixed, public, never registered
        name="MyApp command line",
        scopes=("cli:login",),  # NOT in your ScopeConfig
        resource_path="/api",  # its keys are confined here
        consent_text="It acts as you from your terminal.",
        refresh_idle_ttl=timedelta(days=30),
    ),
)
```

The tool:

1. Listens on `127.0.0.1` (any free port), then opens the browser at
   `/oauth/authorize?response_type=code&client_id=myapp-cli&redirect_uri=
   http://127.0.0.1:<port>/callback&code_challenge=...&code_challenge_method=S256
   &state=...&resource=<issuer>/api&scope=cli:login`.
2. The person signs in (the usual consent sign-in), sees a fixed page ("Sign
   in to the command line?", your `consent_text`, a this-runs-on-your-computer
   warning) and clicks Allow. No checkboxes: the login gets exactly the
   native scopes.
3. The browser comes back to the loopback with `code`, `state` and `iss`.
   The tool checks `state` and `iss`, then trades the code at `/oauth/token`
   (same fields as any client, with `resource=<issuer>/api`) for a one-hour
   access key and an `sgr_` refresh token, and refreshes like any client.
4. Sign out with `POST /oauth/revoke` (`token=<refresh token>`,
   `client_id=myapp-cli`).

Rules:

- Redirects: only `http://127.0.0.1:<port>/callback` and
  `http://[::1]:<port>/callback`. Never `localhost` (it can resolve
  elsewhere), another path, a query or a fragment. Anything else is a page,
  never a redirect.
- `resource` must name `issuer + resource_path` at the consent page; at the
  token endpoint it may be absent, and if present must match.
- Native scopes are refused (`ValueError`) if they are also in your
  `ScopeConfig`: a chat app asking for them gets `invalid_scope`. Your app
  tells a native login apart by its key's scopes (and, to be sure, its
  connection's `client_id`, read with `MatchedKey.connection_id`).
- Its keys are ordinary API keys confined to `resource_path` (403
  `key_confined` elsewhere, including `/auth/*`), holding only the native
  scopes. Your own scope guards will refuse them unless your app maps the
  native scope to the person's own permissions.
- Each sign-in is its own connection (no step-up revoke), lapses after
  `refresh_idle_ttl` unused, and shows under `/auth/connections` with
  `fixed: true`: it can be revoked (`DELETE`), not changed (`PATCH` is 400
  `fixed_connection`).

### Revoking a refresh token

`POST /oauth/revoke` (RFC 7009), form fields `token` and `client_id`, for
any client. A refresh token (current, or retired into the history) revokes
its connection and every key it holds. The answer is always 200 `{}`, also
for an unknown token or another client's token (which revokes nothing).
Discovery lists it as `revocation_endpoint`.

## Service layer: Caller and typed errors

`snapgate.service` is the contract an app's HTTP routes and MCP tools share.
**Functions hold all logic and all permission checks.** Each takes a
`Caller`, checks scope and ownership itself, does the work, and raises the
typed errors below. Routes and tools are thin, parallel adapters. Each does
four things:

1. Authenticate and build the Caller (`caller_for(user, key, SCOPES)`).
2. Convert the input into function arguments.
3. Call the function.
4. Map the result and any typed error to its wire format
   (`install_service_errors` for HTTP, `tool_error_text` for MCP).

```python
from snapgate import Caller, NotFound, caller_for, install_service_errors

WRITE = "deadlines:write"


# service layer: the only place with checks
def set_status(db, caller: Caller, item_id: str, status: str) -> ItemView:
    caller.require(WRITE)  # InsufficientScope unless held
    item = load_visible(db, caller.user, item_id)
    if item is None:
        raise NotFound("no such deadline")
    ...
    db.commit()
    return view


# HTTP adapter
def require_caller(user=Depends(require_approved_user), key=Depends(get_request_api_key)):
    return caller_for(user, key, SCOPES)  # a session (no key) holds every scope


@app.post("/items/status")
def post_status(body: StatusBody, caller: Caller = Depends(require_caller), db=Depends(get_db)):
    return set_status(db, caller, body.id, body.status)


install_service_errors(app)  # one handler for every ServiceError


# MCP adapter (the app glues snapgate to its SDK; snapgate never imports mcp)
async def run(ctx, fn, *args):
    caller = ctx.request_context.request.state.caller  # built once per request
    try:
        return await anyio.to_thread.run_sync(lambda: fn(open_db(), caller, *args))
    except ServiceError as exc:
        raise ToolError(tool_error_text(exc)) from None
```

| Typed error | status | code | MCP tool text |
|---|---|---|---|
| `NotFound` | 404 | `not_found` | `404: <detail> (not_found)` |
| `Forbidden` (object level) | 403 | `forbidden` | `403: <detail> (forbidden)` |
| `InsufficientScope` | 403 + `WWW-Authenticate` | `insufficient_scope` | over OAuth, an HTTP 403 challenge at `/mcp` (`mcp_scope_challenge`); inside a tool, `403: <detail> (insufficient_scope)` |
| `Conflict` | 409 | `conflict` | `409: <detail> (conflict)` |
| `Invalid` | 422 (body adds `field`) | `invalid_input` | `422: <detail> (invalid_input)` |
| `RateLimited` | 429 + `Retry-After` | `rate_limited` | `429: <detail> (rate_limited)` |

- **Subclass for domain errors.** A subclass without its own `code` gets
  `snake(ClassName)`: `class GroupNotFound(NotFound)` is 404
  `group_not_found`. Set `status` for a new status (`class Gone(ServiceError):
  status = 410`). Keyword arguments land in `exc.data` and in the HTTP body
  (`Conflict("stale", current_version=v)`).
- **The Caller.** `Caller(user, scopes, via)`: `scopes` is already expanded
  through `implies`; `via` is `"session"` (browser JWT, every scope),
  `"key"` (Settings-page key) or `"connection"` (an OAuth connection's key).
  `caller.require(*names)` raises `InsufficientScope(names)`. Tests build
  Callers directly (`Caller(user, SCOPES.expand(["x:read"]), "key")`) and call
  the functions with no HTTP or MCP in the way.
- **The HTTP 403 `insufficient_scope`** carries the same `WWW-Authenticate` as
  `InsufficientScopeError` (with `resource_metadata` once the OAuth server is
  installed). `install_service_errors(app, body=...)` reshapes the JSON body;
  status and headers stay snapgate's.
- **MCP over OAuth.** A tool cannot change the HTTP status of the exchange it
  runs in, so a read-only connection calling a write tool should be refused
  before the exchange with `mcp_scope_challenge(request, needed, detail)`.
  That reply is what makes a chat app offer "allow edits". It is a courtesy
  to the person, not the security boundary: the function still refuses.
- **Rate limits** that used to come from route dependencies are charged
  explicitly in the MCP adapter (per tool call), raising `RateLimited`.
- **No loopback.** A tool never calls the app's own HTTP API, in process or
  over the network. If a tool needs a check a route has, move the check
  into the function.
- `create_scope_guard` / `InsufficientScopeError` remain for route-level
  guards in apps that have not moved their checks into functions yet.

## Lifecycle Hooks

Run custom code after auth events without modifying snapgate:

```python
from snapgate import create_snapgate_router, SnapgateHooks


def on_login(user, db):
    """Called after every successful OAuth login (new and returning users)."""
    invalidate_user_cache(user.id)
    log_login(user.email)


def on_user_created(user, db):
    """Called when a new user is created (first-time login only)."""
    send_welcome_email(user.email)
    provision_user_resources(user.id)


hooks = SnapgateHooks(
    on_login=on_login,
    on_user_created=on_user_created,  # Optional
)

router = create_snapgate_router(
    get_db=get_session,
    hooks=hooks,
)
```

**Hook behavior:**
- Hooks can be sync or async functions
- Receive `(user: User, db: Session)` as arguments
- Called AFTER database transaction is committed
- Exceptions in hooks are logged but don't break the auth flow
- For new users, both `on_user_created` and `on_login` fire (in that order)
- For returning users, only `on_login` fires

**`on_sign_in_attempt(db, attempt)`** is different: sync only, run in a
threadpool, inside the sign-in request, AFTER `on_user_created` / `on_login`
(so it sees a status they changed) and BEFORE any token or exchange code is
made. `attempt` is a `SignInAttempt(email, name, user, allowed, reason, ip,
user_agent, method)`: `allowed` is `status == APPROVED`, `reason` is the status
(`pending`, `suspended`, `denied`) or an `AuthError` code such as
`identity_conflict` (then `user` is None). Snapgate commits after it; a failure
is logged and never breaks sign-in. `snapgate.useradmin` uses it for its
sign-in log.

## User admin (optional)

`snapgate.useradmin` adds site admins (from `SNAPGATE_*_EMAILS`), a sign-in log,
approve / suspend / unsuspend, and requests for app roles. Import it only if
you mount it; importing registers its three tables.

```python
from snapgate.useradmin import RoleSpec, UserAdmin, UserAdminHooks, create_useradmin_router

admin = UserAdmin(
    UserAdminHooks(roles={"writer": RoleSpec(label="Writer", holds=..., on_approve=...)})
)
app.include_router(create_snapgate_router(get_db=get_db, hooks=admin.snapgate_hooks(my_hooks)))
app.include_router(create_useradmin_router(get_db, admin))
```

`RoleSpec.on_approve` runs inside the approval's transaction and must not
commit; raising aborts the approval. Apps with SQL migrations copy
`sql/useradmin.sql` into their next numbered migration. Endpoints, error codes
and what suspension revokes are in the root README, "User admin (optional)".

## Integration with App-Specific User Models

Many apps have their own user model with app-specific data (profiles, preferences, permissions). You have two options:

### Option 1: Single Table (Simple Apps)

For simple apps, just add fields to snapgate's User or use it directly. Snapgate's `User` has: `id`, `email`, `name`, `picture`, `status` (UserStatus enum: pending/approved/denied/suspended), `created_at`.

### Option 2: Two Tables (Complex Apps)

For apps with pre-existing users, complex permissions, or domain-specific user data, use **two tables**:

```
┌─────────────────────────┐
│  snapgate.User          │  ← Snapgate handles this (auth identity)
│  - Google OAuth info    │
│  - Approval status      │
└───────────┬─────────────┘
            │ FK link
            ▼
┌─────────────────────────┐
│  app.AppUser            │  ← Your app handles this (app identity)
│  - App-specific data    │
│  - Permissions, roles   │
└─────────────────────────┘
```

**Key principle: Don't reimplement snapgate.** Use snapgate for OAuth, JWT, tokens, refresh. Add only a thin linking layer:

```python
# Your app's user model (in your app, not in snapgate)
class AppUser(SQLModel, table=True):
    id: str
    snapgate_id: Optional[int] = Field(foreign_key="snapgate_users.id")
    # ... your app-specific fields (roles, preferences, etc.)


# Simple lookup function (~5 lines)
def get_app_user(db: Session, snapgate_id: int) -> Optional[AppUser]:
    return db.exec(select(AppUser).where(AppUser.snapgate_id == snapgate_id)).first()


# Use in your routes - snapgate handles auth, you handle the lookup
@app.get("/api/profile")
def get_profile(user: User = Depends(require_approved_user), db: Session = Depends(get_db)):
    app_user = get_app_user(db, user.id)
    if not app_user:
        raise HTTPException(404, "Account not linked to app user")
    return app_user
```

### Extending /auth/me (Optional)

If your frontend needs both auth and app user info in one call, add your own `/auth/me` endpoint **after** mounting snapgate's router (yours will override):

```python
# Mount snapgate first
app.include_router(create_snapgate_router(get_db=get_db))

# Then add your extended /auth/me
@app.get("/auth/me")
def get_me(user: User = Depends(get_current_user_flexible), db: Session = Depends(get_db)):
    if not user:
        return {"user": None, "app_user": None}

    app_user = get_app_user(db, user.id)
    return {
        "user": {"id": user.id, "email": user.email, "name": user.name, ...},
        "app_user": {"id": app_user.id, ...} if app_user else None,
    }
```

### What NOT To Do

Don't reimplement snapgate's functionality in your app:

```python
# BAD - reimplementing what snapgate already provides
def my_create_access_token(...): ...      # Use snapgate's
def my_verify_token(...): ...             # Use snapgate's
def my_oauth_callback(...): ...           # Use snapgate's
def my_refresh_tokens(...): ...           # Use snapgate's

# GOOD - use snapgate, add only app-specific logic
from snapgate import create_snapgate_router, require_approved_user
app.include_router(create_snapgate_router(get_db=get_db))  # All auth handled

def get_app_user(db, snapgate_id): ...    # Only this is app-specific
```

## Table Customization

To use different table names (e.g., `snapgate_users` instead of `users`), modify `models.py`:

```python
class User(SQLModel, table=True):
    __tablename__ = "snapgate_users"  # Custom table name
    ...
```

## Files

- `__init__.py` - Public exports
- `core.py` - Everything: router factory, auth service, OAuth, JWT, exceptions, rate limiting
- `useradmin.py` - Optional user admin (site admins, sign-in log, suspend, role requests)
- `sql/useradmin.sql` - PostgreSQL DDL for the useradmin tables, for apps that migrate by hand

### Rate limiting

`snapgate.limiter` is a slowapi `Limiter` keyed by `snapgate.client_ip`, which
is also exported for apps that add their own limited routes:

```python
from snapgate import client_ip, limiter


@router.post("/chat")
@limiter.limit("20/minute", key_func=client_ip)
def chat(request: Request): ...
```

Snapgate's own routes (`/auth/google`, `/auth/google/callback`,
`/auth/refresh`, `/auth/logout`, `/auth/exchange`, `/oauth/register`,
`/oauth/token`) do not use the slowapi decorator. They count requests with
`enforce_rate_limit(request, name)` on the same limiter storage, using a name
from `auth_rate_limits`. Over the limit, the `/auth/*` routes answer 429 with
`{"detail": {"error": "rate_limited", "message": "..."}}` and `Retry-After: 60`;
the `/oauth/*` routes answer RFC 6749's `temporarily_unavailable`. Your own
`RateLimitExceeded` handler only sees your own routes.

Watch out: slowapi files decorator limits by function name, so an app's own
`@limiter.limit` routes stack one more copy each time a factory that defines
them runs (for example, one app per test). If your routes live in a factory
called more than once, check limits inside the handler with `within_rate_limit`
or `enforce_rate_limit` instead.

Counters are held in-process, so snapgate assumes one worker per deployment.
- `models.py` - SQLModel database models (User, RefreshToken, ApiKey)
- `config.py` - Settings from environment
- `dependencies.py` - FastAPI dependencies (get_current_user, require_user, etc.)
- `api_keys.py` - API key generation and verification
- `scopes.py` - Scope vocabulary, `require_scope`, key confinement
- `oauth.py` - Optional OAuth authorization server for MCP connectors
- `client_metadata.py` - Client ID Metadata Documents (fetch, validate, cache)
- `safe_http.py` - Outbound HTTP pinned to public addresses (SSRF guard)
