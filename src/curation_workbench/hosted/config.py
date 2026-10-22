"""Hosted service settings, from `CWB_*` environment variables.

Snapgate keeps its own env names (`OIDC_ISSUER`, `OIDC_CLIENT_ID`,
`OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URI`, `OIDC_LABEL`, `JWT_SECRET_KEY`,
`SESSION_SECRET_KEY`, `FRONTEND_URL`, `ALLOWED_ORIGINS`,
`RATE_LIMIT_CLIENT_IP_HEADER`, `SNAPGATE_SUPERADMIN_EMAILS`,
`SNAPGATE_ADMIN_EMAILS`, `DEBUG`, `DEMO_LOGIN`); they are never duplicated
here. The database is plan 1's `database_url_from_env()` (`CWB_DATABASE_URL`,
else `POSTGRES_*`).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

#: The built review UI, shipped as package data (`npm --prefix frontend run build`).
DEFAULT_FRONTEND_DIST = Path(__file__).resolve().parents[1] / "web"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CWB_", extra="ignore")

    #: Fail-safe default: an unset env is treated as production.
    env: Literal["dev", "test", "production"] = "production"
    public_url: str = "https://curate.databio.org"
    #: The built SPA folder; default the packaged curation_workbench/web/.
    frontend_dist: Path = DEFAULT_FRONTEND_DIST
    #: Off switch for the in-process rate limits (`CWB_RATE_LIMITS=off`).
    rate_limits: bool = True
    #: Header carrying the real client IP (production: `CF-Connecting-IP`).
    client_ip_header: str | None = None
    #: Seconds between checks of each project's GitHub repo for a new commit
    #: (`CWB_FETCH_SECONDS`). Unset: 300 in production, off (0) otherwise.
    fetch_seconds: int | None = None

    @property
    def fetch_interval(self) -> int:
        if self.fetch_seconds is not None:
            return max(0, self.fetch_seconds)
        return 300 if self.production else 0

    @property
    def production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    """The process-wide settings. Tests call `get_settings.cache_clear()`."""
    return Settings()


__all__ = ["DEFAULT_FRONTEND_DIST", "Settings", "get_settings"]
