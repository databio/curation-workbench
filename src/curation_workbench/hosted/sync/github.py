"""Reading a public GitHub repo over plain HTTPS: no clone, no credentials.

`GitHub().head(repo, branch)` asks the GitHub API for the branch head commit;
`GitHub().file(repo, sha, path)` downloads one file at that commit from
raw.githubusercontent.com. `fetch_snapshot(gh, repo, branch)` reads the repo's
`workbench.yaml` (as data: `yaml.safe_load`), takes its `snapshot:` path and
returns `(sha, bundle)`. Nothing here imports or runs project code.

Tests pass their own object with the same two methods.
"""
from __future__ import annotations

import json
import posixpath
import re
import urllib.error
import urllib.parse
import urllib.request

import yaml

from ...snapshot import check_format

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
USER_AGENT = "curation-workbench (curate.databio.org)"
CONFIG_PATH = "workbench.yaml"
TIMEOUT = 30
MAX_CONFIG_BYTES = 1 << 20
MAX_SNAPSHOT_BYTES = 64 << 20

REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
BRANCH_RE = re.compile(r"^(?!/)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/-]{1,200}(?<!/)(?<!\.lock)$")


class FetchError(Exception):
    """Reading the repo failed; `str(e)` is safe to show and store."""


def parse_repo(value) -> str:
    """`owner/name` from `owner/name`, `https://github.com/owner/name` or the
    same with `.git` or a trailing slash. Raises ValueError otherwise."""
    s = str(value or "").strip()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if s.lower().startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    if not REPO_RE.match(s):
        raise ValueError(f"{value!r} is not a GitHub repo (owner/name or https://github.com/owner/name)")
    return s


def check_branch(branch) -> str:
    b = str(branch or "").strip()
    if not BRANCH_RE.match(b):
        raise ValueError(f"{branch!r} is not a valid branch name")
    return b


def snapshot_path(config: dict) -> str:
    """The `snapshot:` path from a parsed workbench.yaml, relative to the repo
    root (the config sits at the root). Raises FetchError when missing or unsafe."""
    p = config.get("snapshot") if isinstance(config, dict) else None
    if not isinstance(p, str) or not p.strip():
        raise FetchError(f"{CONFIG_PATH} sets no `snapshot:` path (the committed snapshot file)")
    norm = posixpath.normpath(p.strip())
    if norm.startswith(("/", "../")) or norm in ("..", "."):
        raise FetchError(f"{CONFIG_PATH} `snapshot: {p}` must be a path inside the repo")
    return norm


class GitHub:
    """The real thing: GitHub's REST API and raw.githubusercontent.com."""

    def __init__(self, api=API, raw=RAW, timeout=TIMEOUT):
        self.api, self.raw, self.timeout = api.rstrip("/"), raw.rstrip("/"), timeout

    def _get(self, url, accept, limit):
        req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = resp.read(limit + 1)
        except urllib.error.HTTPError as e:
            raise FetchError(f"GET {url}: HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise FetchError(f"GET {url}: {getattr(e, 'reason', e)}") from None
        if len(data) > limit:
            raise FetchError(f"GET {url}: larger than {limit} bytes")
        return data

    def head(self, repo, branch) -> str:
        url = f"{self.api}/repos/{repo}/commits/{urllib.parse.quote(branch, safe='/')}"
        sha = self._get(url, "application/vnd.github.sha", 100).decode("ascii", "replace").strip()
        if not SHA_RE.match(sha):
            raise FetchError(f"GET {url}: not a commit id")
        return sha

    def file(self, repo, sha, path, limit=MAX_SNAPSHOT_BYTES) -> bytes:
        url = f"{self.raw}/{repo}/{sha}/{urllib.parse.quote(path, safe='/')}"
        return self._get(url, "*/*", limit)


def fetch_snapshot(gh, repo, branch, sha=None):
    """(commit sha, bundle) for the branch head (or `sha`)."""
    sha = sha or gh.head(repo, branch)
    try:
        config = yaml.safe_load(gh.file(repo, sha, CONFIG_PATH, MAX_CONFIG_BYTES)) or {}
    except yaml.YAMLError as e:
        raise FetchError(f"{CONFIG_PATH} at {sha[:12]} is not valid YAML: {e}") from None
    path = snapshot_path(config)
    raw = gh.file(repo, sha, path)
    try:
        bundle = check_format(json.loads(raw))
    except ValueError as e:
        raise FetchError(f"{path} at {sha[:12]}: {e}") from None
    return sha, bundle


__all__ = ["CONFIG_PATH", "FetchError", "GitHub", "check_branch", "fetch_snapshot", "parse_repo",
           "snapshot_path"]
