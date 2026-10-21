# SNAPGATE module - do not edit. Overwritten on updates. Customize via SnapgateHooks.
"""
Snapgate - Portable Drop-In Authentication System

⚠️  READ-ONLY MODULE - DO NOT MODIFY THESE FILES
    Customize via SnapgateHooks and environment variables only.
    All app-specific code belongs outside this folder.

Drop-in OpenID Connect sign-in for FastAPI, through one issuer (OIDC_ISSUER).

Usage:
    from snapgate import create_snapgate_router, User
    router = create_snapgate_router(get_db=get_session)
    app.include_router(router)
"""

from .api_keys import ApiKeyMatch, MatchedKey, authenticate_api_key
from .config import SiteRole, settings
from .core import (
    SignInAttempt,
    SnapgateHooks,
    cleanup_expired_codes,
    client_ip,
    create_auth_dependencies,
    create_snapgate_router,
    enforce_rate_limit,
    get_or_create_external_user,
    limiter,
    safe_consent_next,
    set_consent_user,
    sign_in_oidc_user,
    within_rate_limit,
)
from .dependencies import create_flexible_auth_dependencies
from .models import (
    ApiKey,
    ExchangeCode,
    OAuthClient,
    OAuthCode,
    OAuthConnection,
    OAuthRefreshHistory,
    RefreshToken,
    User,
    UserStatus,
)
from .oauth import NativeClient, OAuthServerConfig, create_oauth_router, www_authenticate
from .scopes import (
    InsufficientScopeError,
    KeyConfinedError,
    ScopeConfig,
    ScopeGroup,
    UnknownScopeError,
    create_scope_guard,
    request_within,
)
from .service import (
    Caller,
    Conflict,
    Forbidden,
    InsufficientScope,
    Invalid,
    NotFound,
    RateLimited,
    ServiceError,
    caller_for,
    install_service_errors,
    mcp_scope_challenge,
    tool_error_text,
)

__version__ = "5.1.0"

__all__ = [
    "User",
    "RefreshToken",
    "ApiKey",
    "ExchangeCode",
    "UserStatus",
    "create_snapgate_router",
    "create_auth_dependencies",
    "cleanup_expired_codes",
    "limiter",
    "enforce_rate_limit",
    "within_rate_limit",
    "client_ip",
    "SnapgateHooks",
    "SignInAttempt",
    "SiteRole",
    "settings",
    "create_flexible_auth_dependencies",
    "authenticate_api_key",
    "ApiKeyMatch",
    "MatchedKey",
    "ScopeConfig",
    "ScopeGroup",
    "UnknownScopeError",
    "create_scope_guard",
    "InsufficientScopeError",
    "KeyConfinedError",
    "request_within",
    "OAuthServerConfig",
    "NativeClient",
    "safe_consent_next",
    "set_consent_user",
    "get_or_create_external_user",
    "sign_in_oidc_user",
    "create_oauth_router",
    "www_authenticate",
    "OAuthClient",
    "OAuthCode",
    "OAuthConnection",
    "OAuthRefreshHistory",
    "Caller",
    "caller_for",
    "ServiceError",
    "NotFound",
    "Forbidden",
    "Conflict",
    "Invalid",
    "InsufficientScope",
    "RateLimited",
    "install_service_errors",
    "tool_error_text",
    "mcp_scope_challenge",
]
