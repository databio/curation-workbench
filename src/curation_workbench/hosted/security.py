"""Refuse to boot in production on snapgate's test defaults or dev-only flags."""
from __future__ import annotations

import logging

from .config import Settings

logger = logging.getLogger("curation_workbench.hosted.security")

#: Snapgate settings whose built-in defaults are test-only values.
SENSITIVE_SETTINGS_FIELDS = (
    "JWT_SECRET_KEY",
    "SESSION_SECRET_KEY",
    "OIDC_ISSUER",
    "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET",
)


class InsecureConfigError(RuntimeError):
    """Production boot refused: one or more security settings are unsafe."""


def production_violations(sg_settings, settings: Settings) -> list[str]:
    """Every reason production must refuse to boot. Empty outside production."""
    if settings.env != "production":
        return []
    violations: list[str] = []
    fields = type(sg_settings).model_fields
    for name in SENSITIVE_SETTINGS_FIELDS:
        effective = getattr(sg_settings, name, None)
        if effective == fields[name].default:
            violations.append(f"{name} is still snapgate's built-in test default")
        elif isinstance(effective, str) and (effective.startswith("<") or "<FILL" in effective):
            violations.append(f"{name} looks like an unfilled placeholder")
    if getattr(sg_settings, "DEBUG", False):
        violations.append("DEBUG must be false in production")
    if getattr(sg_settings, "DEMO_LOGIN", False):
        violations.append("DEMO_LOGIN must be false in production (it enables /auth/demo-login)")
    public = settings.public_url.rstrip("/")
    redirect = getattr(sg_settings, "OIDC_REDIRECT_URI", "") or ""
    if not redirect.startswith(public + "/"):
        violations.append(f"OIDC_REDIRECT_URI ({redirect}) does not start with "
                          f"CWB_PUBLIC_URL ({settings.public_url})")
    if not settings.public_url.startswith("https://"):
        violations.append(f"CWB_PUBLIC_URL ({settings.public_url}) must be https in production")
    frontend = (getattr(sg_settings, "FRONTEND_URL", "") or "").rstrip("/")
    if frontend != public:
        violations.append(f"FRONTEND_URL ({frontend}) must equal CWB_PUBLIC_URL; the SPA is same-origin")
    if not (getattr(sg_settings, "SNAPGATE_SUPERADMIN_EMAILS", "") or "").strip():
        violations.append("SNAPGATE_SUPERADMIN_EMAILS is empty: nobody could approve accounts "
                          "or build teams")
    return violations


def validate_production_security(sg_settings, settings: Settings) -> None:
    """Raise `InsecureConfigError` listing every violation at once."""
    violations = production_violations(sg_settings, settings)
    if violations:
        msg = ("refusing to boot with CWB_ENV=production:\n  - " + "\n  - ".join(violations)
               + "\nSet real secrets, or set CWB_ENV=dev for local work.")
        logger.critical(msg)
        raise InsecureConfigError(msg)


__all__ = ["InsecureConfigError", "production_violations", "validate_production_security"]
