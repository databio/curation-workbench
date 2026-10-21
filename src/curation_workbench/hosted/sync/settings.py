"""Sync worker settings, from CWB_* environment variables (see dev-docs/sync.md).

  CWB_ENV                   dev | production (production refuses unsafe settings)
  CWB_SYNC_DIR              mirrors and cached trees (default /var/lib/cwb)
  CWB_RUNNER_URL            the isolated runner (http://curate-runner:8150); unset in dev
                            and tests: jobs run in a local child process instead
  CWB_RUNNER_TIMEOUT        seconds one runner job may take (default 300)
  CWB_WORKER_TICK_SECONDS   loop period (10)
  CWB_WORKER_STUCK_SECONDS  a running job older than this is requeued (900)
  CWB_WORKER_MAX_ATTEMPTS   then failed (3)
  CWB_MAX_TREE_BYTES        archive cap (200 MiB)
  CWB_ALLOWED_GIT_HOSTS     comma list of https hosts residents may use (github.com)
  CWB_GITHUB_READ_TOKEN     optional read-only token for private github.com repos
  CWB_ALLOW_FILE_REPOS      1: allow file:// and local-path repos (tests/dev; refused in production)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _flag(v):
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class SyncSettings:
    env: str = "dev"
    sync_dir: Path = Path("/var/lib/cwb")
    runner_url: str | None = None
    runner_timeout: int = 300
    tick_seconds: float = 10.0
    stuck_seconds: int = 900
    max_attempts: int = 3
    max_tree_bytes: int = 200 * 2**20
    allowed_git_hosts: tuple = ("github.com",)
    github_read_token: str | None = field(default=None, repr=False)
    allow_file_repos: bool = False
    checks_debounce_seconds: float = 5.0

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env

        def get(name, default=None):
            v = (env.get(name) or "").strip()
            return v or default

        hosts = get("CWB_ALLOWED_GIT_HOSTS", "github.com")
        return cls(
            env=get("CWB_ENV", "dev"),
            sync_dir=Path(get("CWB_SYNC_DIR", "/var/lib/cwb")),
            runner_url=get("CWB_RUNNER_URL"),
            runner_timeout=int(get("CWB_RUNNER_TIMEOUT", 300)),
            tick_seconds=float(get("CWB_WORKER_TICK_SECONDS", 10)),
            stuck_seconds=int(get("CWB_WORKER_STUCK_SECONDS", 900)),
            max_attempts=int(get("CWB_WORKER_MAX_ATTEMPTS", 3)),
            max_tree_bytes=int(get("CWB_MAX_TREE_BYTES", 200 * 2**20)),
            allowed_git_hosts=tuple(h.strip().lower() for h in hosts.split(",") if h.strip()),
            github_read_token=get("CWB_GITHUB_READ_TOKEN"),
            allow_file_repos=_flag(get("CWB_ALLOW_FILE_REPOS")),
        )

    @property
    def production(self):
        return self.env == "production"


def worker_violations(s: SyncSettings) -> list[str]:
    """Why a production worker must refuse to start. Empty outside production.

    The worker holds the database password, so in production it never runs
    repo code itself: every snapshot and check goes to the isolated runner."""
    if not s.production:
        return []
    out = []
    url = (s.runner_url or "").strip()
    if not url:
        out.append("CWB_RUNNER_URL is not set: a production worker never runs project code "
                   "in-process (it holds the secrets); point it at the curate-runner service")
    elif not url.startswith(("http://", "https://")):
        out.append(f"CWB_RUNNER_URL ({url}) must be an http:// or https:// URL")
    if s.allow_file_repos:
        out.append("CWB_ALLOW_FILE_REPOS is for tests and dev only")
    return out
