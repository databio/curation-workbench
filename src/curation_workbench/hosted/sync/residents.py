"""Resident projects: the superadmin's service functions.

A resident tells the service where a hosted project's repo is (URL, branch,
`workbench.yaml` path, plugin folder) and which plugin code a superadmin has
approved (by git tree id). Every function takes a `caller` that must be a
superadmin (`caller.is_superadmin`); API keys are never admin. The CLI
(`cwb hosted resident ...`) acts as `OPERATOR`. The API (plan 2) mounts only
`list` and `show`, read-only.

There is no `remove`: disable instead (votes and snapshots refer to a resident).
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy import update as sa_update

from ...store import StoreError
from ...store.pg.tables import projects
from . import gitrepo, jobs
from .publish import decode_bundle
from .settings import SyncSettings
from .tables import cw_derived, cw_plugin_approvals, cw_residents, cw_snapshots

R = cw_residents


@dataclass(frozen=True)
class Caller:
    """Who is acting. Plan 2 maps its snapgate session onto this shape."""
    is_superadmin: bool
    user_id: int | None = None
    label: str = ""


OPERATOR = Caller(is_superadmin=True, user_id=None, label="operator (cli)")


def _admin(caller):
    if not getattr(caller, "is_superadmin", False):
        raise StoreError(403, ["resident administration needs a superadmin"])


def _settings(settings):
    return settings or SyncSettings.from_env()


def _row(conn, slug, lock=False):
    q = (select(R, projects.c.slug, projects.c.name.label("project_name"))
         .join(projects, projects.c.id == R.c.project_id).where(projects.c.slug == slug))
    if lock:
        q = q.with_for_update(of=R)
    row = conn.execute(q).one_or_none()
    if row is None:
        raise StoreError(404, [f"no resident for project {slug}"])
    return dict(row._mapping)


def active_approval(conn, resident):
    """The newest approval newer than any revoke, as a dict, or None."""
    q = (select(cw_plugin_approvals).where(cw_plugin_approvals.c.resident_id == resident["id"])
         .order_by(cw_plugin_approvals.c.approved_at.desc(), cw_plugin_approvals.c.id.desc()).limit(1))
    if resident.get("plugins_revoked_at") is not None:
        q = q.where(cw_plugin_approvals.c.approved_at > resident["plugins_revoked_at"])
    row = conn.execute(q).one_or_none()
    return dict(row._mapping) if row else None


def _public(r):
    """A resident row without its webhook secret."""
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v)
            for k, v in r.items() if k != "webhook_secret"}


def _repo(settings, r):
    return gitrepo.for_resident(_settings(settings), r)


# ------------------------------------------------------------------ create / change

def add(engine, caller, project_slug, repo_url, branch, config_path="workbench.yaml",
        plugin_dir=None, *, settings=None, poll_seconds=300):
    """Register a resident: validate the URL, prove access with one ls-remote,
    create the row with a webhook secret and queue a manual sync. Returns
    {"resident": ..., "webhook_secret": ...}; the secret is never shown again."""
    _admin(caller)
    s = _settings(settings)
    try:
        gitrepo.check_url(repo_url, s.allowed_git_hosts, allow_file=s.allow_file_repos and not s.production)
        gitrepo.check_branch(branch)
    except gitrepo.GitError as e:
        raise StoreError(400, [e.message]) from None
    for label, p in (("config path", config_path), ("plugin dir", plugin_dir)):
        if p is not None and (p.startswith("/") or ".." in p.split("/") or not p.strip("/")):
            raise StoreError(400, [f"{label} must be a relative path inside the repo, got {p!r}"])
    with engine.connect() as conn:
        proj = conn.execute(select(projects.c.id).where(projects.c.slug == project_slug)).one_or_none()
    if proj is None:
        raise StoreError(404, [f"no project {project_slug} (create it with `cwb db project add`)"])
    probe = gitrepo.GitRepo(s.sync_dir / "probe.git", repo_url, branch, sync_dir=s.sync_dir,
                            token=s.github_read_token, allow_file=s.allow_file_repos)
    try:
        head = probe.head()
    except gitrepo.GitError as e:
        raise StoreError(400, [f"cannot read {repo_url} ({branch}): {e.message}"]) from None
    secret = secrets.token_hex(32)
    with engine.begin() as conn:
        if conn.execute(select(R.c.id).where(R.c.project_id == proj.id)).first():
            raise StoreError(409, [f"project {project_slug} already has a resident; use update"])
        rid = conn.execute(R.insert().values(
            project_id=proj.id, repo_url=repo_url, branch=branch, config_path=config_path,
            plugin_dir=plugin_dir.strip("/") if plugin_dir else None, poll_seconds=poll_seconds,
            webhook_secret=secret, last_seen_commit=head,
            created_by=getattr(caller, "user_id", None)).returning(R.c.id)).scalar_one()
        jobs.enqueue(conn, rid, "commit", "manual", commit_sha=head)
        r = _row(conn, project_slug)
    return {"resident": _public(r), "webhook_secret": secret}


def update_resident(engine, caller, slug, **changes):
    """Change branch, config_path, plugin_dir, poll_seconds or enabled."""
    _admin(caller)
    allowed = {"branch", "config_path", "plugin_dir", "poll_seconds", "enabled"}
    bad = set(changes) - allowed
    if bad:
        raise StoreError(400, [f"cannot update {sorted(bad)}"])
    changes = {k: v for k, v in changes.items() if v is not None}
    if "branch" in changes:
        try:
            gitrepo.check_branch(changes["branch"])
        except gitrepo.GitError as e:
            raise StoreError(400, [e.message]) from None
    with engine.begin() as conn:
        r = _row(conn, slug, lock=True)
        if changes:
            conn.execute(sa_update(R).where(R.c.id == r["id"]).values(updated_at=func.now(), **changes))
            if r["enabled"] or changes.get("enabled"):
                jobs.enqueue(conn, r["id"], "commit", "manual")
        return _public(_row(conn, slug))


def rotate_webhook_secret(engine, caller, slug):
    _admin(caller)
    secret = secrets.token_hex(32)
    with engine.begin() as conn:
        r = _row(conn, slug, lock=True)
        conn.execute(sa_update(R).where(R.c.id == r["id"]).values(webhook_secret=secret,
                                                               updated_at=func.now()))
    return secret


def sync(engine, caller, slug):
    """Queue a manual commit sync; returns the job id (None if one is already live)."""
    _admin(caller)
    with engine.begin() as conn:
        r = _row(conn, slug)
        return jobs.enqueue(conn, r["id"], "commit", "manual")


# ------------------------------------------------------------------ plugins

def plugin_diff(engine, caller, slug, *, settings=None):
    """Fetch, then the diff from the approved plugin tree to the one at the
    branch head (every file when nothing is approved yet)."""
    _admin(caller)
    with engine.connect() as conn:
        r = _row(conn, slug)
        appr = active_approval(conn, r)
    if not r["plugin_dir"]:
        raise StoreError(400, [f"{slug} has no plugin_dir"])
    repo = _repo(settings, r)
    try:
        head = repo.fetch()
        tree = repo.plugin_tree(head, r["plugin_dir"])
        if tree is None:
            raise StoreError(400, [f"{r['plugin_dir']} is not a folder at {head}"])
        text = repo.diff(appr["tree_sha"] if appr else None, tree)
    except gitrepo.GitError as e:
        raise StoreError(502, [e.message]) from None
    return {"head": head, "head_tree": tree, "approved_tree": appr["tree_sha"] if appr else None,
            "diff": text}


def approve_plugin(engine, caller, slug, tree_sha, *, settings=None):
    """Approve the plugin tree `tree_sha` (it must be in the mirror, i.e. at some
    fetched commit), store its files and queue a sync."""
    _admin(caller)
    if not isinstance(tree_sha, str) or not gitrepo.SHA_RE.match(tree_sha):
        raise StoreError(400, ["tree must be a 40-character git tree id"])
    with engine.connect() as conn:
        r = _row(conn, slug)
    repo = _repo(settings, r)
    try:
        files = repo.tree_tar_gz(tree_sha)
    except gitrepo.GitError as e:
        raise StoreError(400, [e.message]) from None
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    with engine.begin() as conn:
        stmt = pg_insert(cw_plugin_approvals).values(
            resident_id=r["id"], tree_sha=tree_sha, files=files,
            approved_by=getattr(caller, "user_id", None))
        conn.execute(stmt.on_conflict_do_update(
            constraint="uq_cw_plugin_approvals",
            set_={"approved_at": func.now(), "approved_by": stmt.excluded.approved_by,
                  "files": stmt.excluded.files}))
        jobs.enqueue(conn, r["id"], "commit", "manual")
    return {"approved_tree": tree_sha}


def revoke_plugin(engine, caller, slug):
    """Stop running plugin code: approvals stay, but none older than now is
    active, so the next sync (queued here) runs generic-only."""
    _admin(caller)
    with engine.begin() as conn:
        r = _row(conn, slug, lock=True)
        conn.execute(sa_update(R).where(R.c.id == r["id"]).values(plugins_revoked_at=func.now(),
                                                               updated_at=func.now()))
        jobs.enqueue(conn, r["id"], "commit", "manual")
    return {"revoked": True}


# ------------------------------------------------------------------ reads

def _snapshot_summary(conn, snapshot_id):
    if snapshot_id is None:
        return None
    s = conn.execute(select(cw_snapshots.c.id, cw_snapshots.c.commit_sha, cw_snapshots.c.commit_time,
                            cw_snapshots.c.schema_fingerprint, cw_snapshots.c.plugin_tree_used,
                            cw_snapshots.c.plugin_tree_at_commit, cw_snapshots.c.warnings,
                            cw_snapshots.c.created_at)
                     .where(cw_snapshots.c.id == snapshot_id)).one()
    return _public(dict(s._mapping))


def list_residents(engine, caller):
    _admin(caller)
    with engine.connect() as conn:
        slugs = conn.execute(select(projects.c.slug).join(R, R.c.project_id == projects.c.id)
                             .order_by(projects.c.slug)).scalars().all()
        out = []
        for slug in slugs:
            r = _row(conn, slug)
            snap = _snapshot_summary(conn, r["current_snapshot_id"])
            out.append({**_public(r), "current_commit": snap["commit_sha"] if snap else None})
        return out


def show(engine, caller, slug, *, settings=None, with_git=True):
    """The resident, its current snapshot (commit, fingerprint, plugins, warnings),
    the derived row's versions, the last jobs and the plugin status."""
    _admin(caller)
    with engine.connect() as conn:
        r = _row(conn, slug)
        snap = _snapshot_summary(conn, r["current_snapshot_id"])
        if snap:
            bundle = decode_bundle(conn.execute(select(cw_snapshots.c.bundle).where(
                cw_snapshots.c.id == snap["id"])).scalar_one())
            snap["plugins"] = bundle.get("plugins") or []
            snap["sources"] = bundle.get("sources") or []
        derived = conn.execute(select(cw_derived.c.snapshot_id, cw_derived.c.decisions_version,
                                      cw_derived.c.checks_decisions_version, cw_derived.c.computed_at)
                               .where(cw_derived.c.resident_id == r["id"])).one_or_none()
        appr = active_approval(conn, r)
        last = jobs.recent(conn, r["id"])
    head_tree = None
    if with_git and r["plugin_dir"] and r["last_seen_commit"] and not r["repo_url"].startswith("dev:"):
        try:
            head_tree = _repo(settings, r).plugin_tree(r["last_seen_commit"], r["plugin_dir"])
        except gitrepo.GitError:
            head_tree = None
    return {
        "resident": _public(r),
        "snapshot": snap,
        "derived": _public(dict(derived._mapping)) if derived else None,
        "plugin": {"approved_tree": appr["tree_sha"] if appr else None,
                   "approved_at": appr["approved_at"].isoformat() if appr else None,
                   "revoked_at": r["plugins_revoked_at"].isoformat() if r["plugins_revoked_at"] else None,
                   "tree_at_head": head_tree},
        "jobs": [_public(j) for j in last],
    }


# ------------------------------------------------------------------ dev

def ensure_dev_resident(conn, project_id, path, *, settings=None):
    """Dev only (refused when CWB_ENV=production): create or reuse a disabled
    resident with `repo_url = "dev:<path>"` that the worker never polls.
    Plan 2's `cwb hosted admin dev-snapshot` publishes to it."""
    if _settings(settings).production:
        raise StoreError(403, ["dev residents are refused when CWB_ENV=production"])
    url = f"dev:{path}"
    row = conn.execute(select(R.c.id, R.c.repo_url).where(R.c.project_id == project_id)).one_or_none()
    if row is not None:
        if not row.repo_url.startswith("dev:"):
            raise StoreError(409, [f"project {project_id} already has a real resident"])
        if row.repo_url != url:
            conn.execute(sa_update(R).where(R.c.id == row.id).values(repo_url=url, updated_at=func.now()))
        return row.id
    return conn.execute(R.insert().values(project_id=project_id, repo_url=url, branch="dev",
                                          enabled=False).returning(R.c.id)).scalar_one()


# The names the plan's interface uses.
list = list_residents  # noqa: A001
update = update_resident
