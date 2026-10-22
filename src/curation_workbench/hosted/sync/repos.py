"""Project repos: register one, fetch its committed snapshot, and the poller.

A superadmin registers a project with its public GitHub repo and branch
(`register`; the admin page's form, or `cwb hosted project add`). The API
process then keeps it current: `sync_project` asks GitHub for the branch head
and, when it is a new commit, downloads the snapshot file the repo commits
(`github.fetch_snapshot`) and publishes it. `Poller` does that for every
project on a timer (`CWB_FETCH_SECONDS`), in a background thread of the API
process. Plain HTTPS JSON only: nothing here clones a repo or runs its code.

A project whose repo is `dev:<label>` is a local dev project
(`cwb hosted admin dev-snapshot`); it is never fetched.
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ...store import StoreError
from ...store.pg import projects as pg_projects
from ...store.pg.tables import projects
from ..service import roles
from . import github, publish
from .tables import cw_repos, cw_snapshots

logger = logging.getLogger("curation_workbench.hosted.repos")
DEV_PREFIX = "dev:"


def _caller_id(caller):
    user = getattr(caller, "user", None)
    return getattr(user, "id", None)


def _ts(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def repo_info(conn, project_id) -> dict:
    """{repo, branch, commit, synced_at, last_checked_at, last_error, ...};
    all None when the project has no repo."""
    row = conn.execute(
        select(cw_repos, cw_snapshots.c.commit_sha, cw_snapshots.c.schema_fingerprint,
               cw_snapshots.c.created_at.label("synced_at"), cw_snapshots.c.warnings)
        .select_from(cw_repos.outerjoin(cw_snapshots,
                                        cw_snapshots.c.id == cw_repos.c.current_snapshot_id))
        .where(cw_repos.c.project_id == project_id)).one_or_none()
    if row is None:
        return {"repo": None, "branch": None, "snapshot_id": None, "commit": None,
                "schema_fingerprint": None, "synced_at": None, "last_checked_at": None,
                "last_error": None, "warnings": []}
    return {"repo": row.repo, "branch": row.branch, "snapshot_id": row.current_snapshot_id,
            "commit": row.commit_sha, "schema_fingerprint": row.schema_fingerprint,
            "synced_at": _ts(row.synced_at), "last_checked_at": _ts(row.last_checked_at),
            "last_error": row.last_error, "warnings": list(row.warnings or [])}


def repo_web_url(repo) -> str | None:
    if not repo or repo.startswith(DEV_PREFIX):
        return None
    return f"https://github.com/{repo}"


# ------------------------------------------------------------------ register

def register(engine, caller, slug, name, repo, branch="master", *, gh=None, fetch=True) -> dict:
    """Create the project (or reuse it) and set its repo and branch, then
    fetch its snapshot once. A repo GitHub cannot read is refused; a snapshot
    that fails to load is kept as `last_error` (the registration stands).
    Returns {"project": {...}, "repo": repo_info}."""
    roles.require_superadmin(caller)
    try:
        repo = github.parse_repo(repo)
        branch = github.check_branch(branch)
    except ValueError as e:
        raise StoreError(400, [str(e)]) from None
    gh = gh or github.GitHub()
    if fetch:
        try:
            gh.head(repo, branch)
        except github.FetchError as e:
            raise StoreError(400, [f"cannot read {repo} ({branch}) on GitHub: {e}"]) from None
    project = pg_projects.get_project(engine, slug)
    if project is None:
        project = pg_projects.create_project(engine, slug, name)
    with engine.begin() as conn:
        _set_repo(conn, project["id"], repo, branch, _caller_id(caller))
    if fetch:
        sync_project(engine, slug, gh=gh)
    with engine.connect() as conn:
        return {"project": project, "repo": repo_info(conn, project["id"])}


def _set_repo(conn, project_id, repo, branch, created_by=None):
    stmt = pg_insert(cw_repos).values(project_id=project_id, repo=repo, branch=branch,
                                      created_by=created_by)
    conn.execute(stmt.on_conflict_do_update(
        index_elements=[cw_repos.c.project_id],
        set_={"repo": repo, "branch": branch, "last_commit": None, "last_error": None,
              "updated_at": func.now()}))


def ensure_dev_repo(conn, project_id, label):
    """Dev only: a `dev:<label>` repo row that is never fetched (dev-snapshot)."""
    row = conn.execute(select(cw_repos.c.repo).where(cw_repos.c.project_id == project_id)
                       ).scalar_one_or_none()
    if row is not None and not row.startswith(DEV_PREFIX):
        raise StoreError(409, [f"project {project_id} already has a GitHub repo"])
    _set_repo(conn, project_id, f"{DEV_PREFIX}{label}", "dev")


# ------------------------------------------------------------------ fetch

def _project_id(engine, slug):
    with engine.connect() as conn:
        pid = conn.execute(select(projects.c.id).where(projects.c.slug == slug)).scalar_one_or_none()
    if pid is None:
        raise StoreError(404, [f"no project {slug}"])
    return pid


def sync_project(engine, slug, *, gh=None, force=False) -> dict:
    """Check the branch head; publish its snapshot when it is new (or `force`).
    Errors land in `last_error`, never raise. Returns
    {"commit", "published": bool, "error"}."""
    pid = _project_id(engine, slug)
    with engine.connect() as conn:
        info = repo_info(conn, pid)
    if info["repo"] is None:
        raise StoreError(404, [f"project {slug} has no repo; register it first"])
    if info["repo"].startswith(DEV_PREFIX):
        return {"commit": info["commit"], "published": False, "error": None}
    gh = gh or github.GitHub()
    sha, published, error = None, False, None
    try:
        sha = gh.head(info["repo"], info["branch"])
        if force or sha != info["commit"]:
            sha, bundle = github.fetch_snapshot(gh, info["repo"], info["branch"], sha)
            with engine.begin() as conn:
                publish.publish_snapshot(conn, pid, bundle, sha)
            published = True
    except github.FetchError as e:
        error = str(e)
    except Exception as e:  # noqa: BLE001 - a bad snapshot must not stop the poller
        logger.exception("publishing %s failed", slug)
        error = f"{type(e).__name__}: {e}"
    with engine.begin() as conn:
        values = {"last_checked_at": func.now(), "last_error": error[:4000] if error else None}
        if sha:
            values["last_commit"] = sha
        conn.execute(update(cw_repos).where(cw_repos.c.project_id == pid).values(**values))
    logger.info("fetch %s repo=%s sha=%s published=%s error=%s", slug, info["repo"],
                (sha or "-")[:12], published, error or "-")
    return {"commit": sha, "published": published, "error": error}


def sync_all(engine, *, gh=None) -> dict:
    """`sync_project` for every live project with a GitHub repo; {slug: result}."""
    with engine.connect() as conn:
        slugs = conn.execute(
            select(projects.c.slug).join(cw_repos, cw_repos.c.project_id == projects.c.id)
            .where(projects.c.archived_at.is_(None), ~cw_repos.c.repo.startswith(DEV_PREFIX))
            .order_by(projects.c.slug)).scalars().all()
    return {s: sync_project(engine, s, gh=gh) for s in slugs}


class Poller:
    """`sync_all` every `seconds` in a daemon thread (the API's lifespan starts it)."""

    def __init__(self, engine, seconds, gh=None):
        self.engine, self.seconds, self.gh = engine, seconds, gh
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name="cwb-poller", daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            try:
                sync_all(self.engine, gh=self.gh)
            except Exception:  # noqa: BLE001 - the loop must survive a bad pass
                logger.exception("repo poll failed")
            self._stop.wait(self.seconds)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


# ------------------------------------------------------------------ reads

def list_repos(engine, caller) -> list[dict]:
    roles.require_superadmin(caller)
    out = []
    with engine.connect() as conn:
        rows = conn.execute(select(projects.c.id, projects.c.slug, projects.c.name)
                            .join(cw_repos, cw_repos.c.project_id == projects.c.id)
                            .order_by(projects.c.slug)).all()
        for r in rows:
            out.append({"slug": r.slug, "name": r.name, **repo_info(conn, r.id)})
    return out


def show(engine, caller, slug) -> dict:
    roles.require_superadmin(caller)
    pid = _project_id(engine, slug)
    with engine.connect() as conn:
        info = repo_info(conn, pid)
        if info["snapshot_id"] is not None:
            bundle = publish.load_bundle(conn, info["snapshot_id"])
            info["plugins"] = bundle.get("plugins") or []
            info["sources"] = bundle.get("sources") or []
    return {"slug": slug, **info}


__all__ = ["DEV_PREFIX", "Poller", "ensure_dev_repo", "list_repos", "register", "repo_info",
           "repo_web_url", "show", "sync_all", "sync_project"]
