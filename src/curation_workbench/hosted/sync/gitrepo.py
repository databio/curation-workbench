"""A resident's repo, as the worker sees it: one bare mirror, never checked out.

`$CWB_SYNC_DIR/repos/<resident_id>.git`, created with `git init --bare` and
`origin` set to the resident's URL; only the tracked branch is fetched. The
worker never runs anything from the repo: it lists the branch head, fetches,
and exports a commit with `git archive`, rewritten in Python (`rewrite_tree`)
before it goes to the isolated runner.

Every git call runs with a fixed environment (no prompts, no system or global
config, an empty HOME), hooks off, only https allowed (plus file:// in the
test/dev mode), no submodules, no symlink checkout, and a timeout (60 s for
ls-remote, 300 s for fetch). A read token for private GitHub repos goes only
through GIT_ASKPASS, never on a command line or in a URL.
"""
from __future__ import annotations

import gzip
import io
import os
import re
import stat
import subprocess
import tarfile
from pathlib import Path
from urllib.parse import urlparse

from ...runner import Member, link_stays_inside, safe_name

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
LS_REMOTE_TIMEOUT = 60
FETCH_TIMEOUT = 300
LOCAL_TIMEOUT = 120


class GitError(Exception):
    """A git operation or archive that failed. `code`: git_failed, git_timeout,
    too_large, path_escape, bad_repo."""

    def __init__(self, message, code="git_failed"):
        super().__init__(message)
        self.code = code
        self.message = message


def check_branch(branch):
    if not isinstance(branch, str) or not BRANCH_RE.match(branch) or ".." in branch \
            or branch.endswith((".", "/", ".lock")) or "//" in branch:
        raise GitError(f"not a valid branch name: {branch!r}", "bad_repo")
    return branch


def check_url(url, allowed_hosts=("github.com",), allow_file=False):
    """The URL, if a resident may use it: https on an allowed host, or (test/dev
    mode only) a file:// URL or an absolute local path."""
    if not isinstance(url, str) or not url or any(c in url for c in "\x00\n\r \t"):
        raise GitError(f"not a repo URL: {url!r}", "bad_repo")
    if allow_file and (url.startswith("file://") or url.startswith("/")):
        return url
    p = urlparse(url)
    if p.scheme != "https":
        raise GitError(f"repo URL must be https://, got {url!r}", "bad_repo")
    if p.username or p.password:
        raise GitError("repo URL must not carry credentials", "bad_repo")
    host = (p.hostname or "").lower()
    if host not in {h.lower() for h in allowed_hosts}:
        raise GitError(f"repo host {host!r} is not allowed (CWB_ALLOWED_GIT_HOSTS: "
                       f"{', '.join(allowed_hosts)})", "bad_repo")
    return url


def _gz_tar_members(data):
    """Regular files and folders of a gzip tar (approved plugin files)."""
    out = []
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(data)), mode="r:") as tar:
        for m in tar:
            name = safe_name(m.name.removesuffix("/"))
            if m.isdir():
                out.append(Member(name, "dir", None))
            elif m.isreg():
                fh = tar.extractfile(m)
                out.append(Member(name, "file", fh.read() if fh else b""))
            elif m.issym() and link_stays_inside(name, m.linkname):
                out.append(Member(name, "symlink", m.linkname))
            else:
                raise GitError(f"approved plugin file {name} is a link or special file", "path_escape")
    return out


def rewrite_tree(tar_bytes, plugin_dir=None, overlay=None, max_bytes=200 * 2**20):
    """[Member] for a `git archive` tar: `.git*` entries dropped, the
    `plugin_dir` subtree dropped and replaced by `overlay` (a gzip tar of the
    approved plugin tree) when given; absolute paths, `..`, hard links,
    devices and symlinks that leave the tree refused; content capped."""
    pd = safe_name(plugin_dir.strip("/")) if plugin_dir else None

    def in_plugin_dir(name):
        return pd is not None and (name == pd or name.startswith(pd + "/"))

    out, total = [], 0
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tar:
            for m in tar:
                try:
                    name = safe_name(m.name.removesuffix("/"))
                except Exception:
                    raise GitError(f"unsafe path in the repo: {m.name!r}", "path_escape") from None
                if any(part.startswith(".git") for part in name.split("/")):
                    continue
                if in_plugin_dir(name):
                    continue
                if m.isdir():
                    out.append(Member(name, "dir", None))
                elif m.issym():
                    if not link_stays_inside(name, m.linkname):
                        raise GitError(f"{name} is a symlink that leaves the repo", "path_escape")
                    out.append(Member(name, "symlink", m.linkname))
                elif m.isreg():
                    total += m.size
                    if total > max_bytes:
                        raise GitError(f"the repo tree is larger than {max_bytes} bytes "
                                       "(CWB_MAX_TREE_BYTES)", "too_large")
                    fh = tar.extractfile(m)
                    out.append(Member(name, "file", fh.read() if fh else b""))
                else:
                    raise GitError(f"{name} is a hard link or special file", "path_escape")
    except tarfile.TarError as e:
        raise GitError(f"git archive output is not a tar: {e}") from None
    if pd is not None and overlay is not None:
        out.append(Member(pd, "dir", None))
        for m in _gz_tar_members(overlay):
            if m.kind == "file":
                total += len(m.data)
            out.append(Member(f"{pd}/{m.name}", m.kind, m.data))
        if total > max_bytes:
            raise GitError(f"the repo tree is larger than {max_bytes} bytes", "too_large")
    return out


class GitRepo:
    def __init__(self, path, url, branch, *, sync_dir=None, token=None, allow_file=False,
                 max_tree_bytes=200 * 2**20):
        self.path = Path(path)
        self.url = url
        self.branch = check_branch(branch)
        self.allow_file = allow_file
        self.max_tree_bytes = max_tree_bytes
        base = Path(sync_dir) if sync_dir else self.path.parent
        self.home = base / "home"
        self.token = token if url.startswith("https://github.com/") else None

    # ------------------------------------------------------------ plumbing

    def _env(self):
        self.home.mkdir(parents=True, exist_ok=True)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home),
               "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": os.devnull, "LANG": "C.UTF-8"}
        if self.token:
            askpass = self.home / "askpass.sh"
            if not askpass.exists():
                askpass.write_text('#!/bin/sh\ncase "$1" in\n  Username*) echo x-access-token ;;\n'
                                   '  *) printf "%s\\n" "$CWB_GIT_ASKPASS_TOKEN" ;;\nesac\n')
                askpass.chmod(stat.S_IRWXU)
            env["GIT_ASKPASS"] = str(askpass)
            env["CWB_GIT_ASKPASS_TOKEN"] = self.token
        return env

    def _cmd(self, args, bare=True):
        cfg = ["-c", "core.hooksPath=/dev/null", "-c", "protocol.allow=never",
               "-c", "protocol.https.allow=always", "-c", "submodule.recurse=false",
               "-c", "core.symlinks=false", "-c", "credential.helper="]
        if self.allow_file:
            cfg += ["-c", "protocol.file.allow=always"]
        where = [f"--git-dir={self.path}"] if bare else []
        return ["git", *cfg, *where, *args]

    def _git(self, *args, timeout=LOCAL_TIMEOUT, bare=True, check=True):
        try:
            out = subprocess.run(self._cmd(list(args), bare=bare), env=self._env(),
                                 capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise GitError(f"git {args[0]} timed out after {timeout} s", "git_timeout") from None
        if check and out.returncode != 0:
            err = out.stderr.decode("utf-8", "replace").strip()
            if self.token:
                err = err.replace(self.token, "***")
            raise GitError(f"git {args[0]} failed: {err[-2000:]}")
        return out

    # ------------------------------------------------------------ remote

    def head(self):
        """The tracked branch's head on the remote (`git ls-remote`, cheap)."""
        ref = f"refs/heads/{self.branch}"
        out = self._git("ls-remote", "--", self.url, ref, timeout=LS_REMOTE_TIMEOUT, bare=False)
        for line in out.stdout.decode().splitlines():
            sha, _, name = line.partition("\t")
            if name == ref and SHA_RE.match(sha):
                return sha
        raise GitError(f"branch {self.branch} not found at {self.url}", "bad_repo")

    def ensure(self):
        if not (self.path / "HEAD").exists():
            self.path.mkdir(parents=True, exist_ok=True)
            self._git("init", "--bare", "--quiet", str(self.path), bare=False)
            self._git("remote", "add", "origin", self.url)
        else:
            self._git("remote", "set-url", "origin", self.url)
        return self

    def fetch(self):
        """Fetch the tracked branch; return its head commit (as fetched, never a webhook's sha)."""
        self.ensure()
        b = self.branch
        self._git("fetch", "--no-tags", "--prune", "--quiet", "origin",
                  f"+refs/heads/{b}:refs/heads/{b}", timeout=FETCH_TIMEOUT)
        return self.resolve()

    # ------------------------------------------------------------ local reads

    def resolve(self, rev=None):
        out = self._git("rev-parse", "--verify", "--quiet", f"{rev or 'refs/heads/' + self.branch}^{{commit}}")
        return out.stdout.decode().strip()

    def object_type(self, obj):
        out = self._git("cat-file", "-t", obj, check=False)
        return out.stdout.decode().strip() if out.returncode == 0 else None

    def commit_time(self, sha):
        return self._git("log", "-1", "--format=%cI", sha).stdout.decode().strip() or None

    def show(self, sha, path):
        """A file's bytes at `sha`, or None if absent."""
        out = self._git("cat-file", "blob", f"{sha}:{path}", check=False)
        return out.stdout if out.returncode == 0 else None

    def plugin_tree(self, sha, plugin_dir):
        """The git tree id of `plugin_dir` at `sha`, or None if it is not a folder there."""
        if not plugin_dir:
            return None
        spec = f"{sha}:{plugin_dir.strip('/')}"
        if self.object_type(spec) != "tree":
            return None
        return self._git("rev-parse", spec).stdout.decode().strip()

    def tree_tar_gz(self, tree_sha):
        """A gzip tar of one tree (the files an approval stores)."""
        if self.object_type(tree_sha) != "tree":
            raise GitError(f"{tree_sha} is not a tree in the mirror (fetch first?)", "bad_repo")
        return gzip.compress(self._git("archive", "--format=tar", tree_sha).stdout, mtime=0)

    def diff(self, old_tree, new_tree):
        out = self._git("diff", "--no-color", "--no-ext-diff", old_tree or EMPTY_TREE, new_tree)
        return out.stdout.decode("utf-8", "replace")

    def archive(self, sha, plugin_dir=None, overlay=None):
        """[Member] of the tree at `sha`, rewritten by `rewrite_tree`."""
        data = self._git("archive", "--format=tar", sha, timeout=FETCH_TIMEOUT).stdout
        return rewrite_tree(data, plugin_dir, overlay, self.max_tree_bytes)


def for_resident(settings, resident):
    """The GitRepo for a resident row (mapping with id, repo_url, branch)."""
    return GitRepo(Path(settings.sync_dir) / "repos" / f"{resident['id']}.git",
                   resident["repo_url"], resident["branch"], sync_dir=settings.sync_dir,
                   token=settings.github_read_token, allow_file=settings.allow_file_repos,
                   max_tree_bytes=settings.max_tree_bytes)
