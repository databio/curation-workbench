"""The isolated runner: ``cwb runner [--host H] [--port 8150]``.

Every piece of code that comes from a project's repo (its plugin modules,
and the generic code reading its files) runs here and only here in hosted
mode. In production it is its own Swarm service, ``curate-runner``, with no
secrets, no network route out, a read-only root, a size-capped tmpfs ``/tmp``,
no capabilities and a non-root user. The sync worker (which holds the database
password) talks to it over the stack's internal network and never runs repo
code itself. Built on Pigeon's isolated-builder pattern.

Endpoints:

- ``GET /health``: ``{"status": "ok", "busy": bool}``.
- ``GET /self-check``: what this process can reach (secret paths, secret-like
  environment variable names, outbound connections, a write outside /tmp,
  the uid); ``cwb hosted isolation-check`` reads it.
- ``POST /run``: an uncompressed tar holding ``job.json`` and ``tree/<path>``
  (the project checkout). One job at a time: a second ``POST`` gets 409.

``job.json``: ``{"mode", "config_path", "plugins_enabled", "decisions"}``.

- ``mode: snapshot``: open the workbench in hosted mode and answer 200 with the
  gzip JSON bundle (``snapshot.build``), plus a warning when generated files
  committed in the repo are out of date.
- ``mode: checks``: validate each record in ``decisions`` with the plugins'
  decision hooks (``store.validate(..., check_hashes=False, wb=wb)``); answer
  200 with JSON ``{"checks": {decision id: [error]}}``.
- ``mode: apply``: reserved for the "apply approved decisions and open a pull
  request" step; 400 for now.

Errors are JSON ``{"error": {"code", "message", "detail"}}``. Codes:
``config_invalid``, ``path_escape``, ``plugin_import_failed``,
``registry_error`` (422), ``timeout`` (504), ``too_large`` (413),
``bad_request`` (400), ``busy`` (409), ``internal`` (500). ``detail`` holds a
traceback cut to 4000 characters; it never holds secrets because the runner
has none.

Each job runs in a child process (``python -m curation_workbench.runner --job
DIR``) in a fresh folder under the temp dir, removed afterwards, killed at
``CWB_RUNNER_TIMEOUT`` seconds (default 300).

Tars are read member by member: regular files, folders, and symlinks whose
target stays inside the tree; no absolute paths, no ``..``, bounded size
(``CWB_MAX_TREE_BYTES``, default 200 MiB) and member count.

This module imports only the standard library and the core
``curation_workbench`` package, never ``curation_workbench.hosted``,
SQLAlchemy or snapgate (tests/test_module_boundaries.py checks).
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import logging
import os
import posixpath
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import traceback
from collections import namedtuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath

logger = logging.getLogger("curation_workbench.runner")

DEFAULT_PORT = 8150
JOB_JSON = "job.json"
RESULT_JSON = "result.json"
BUNDLE_FILE = "bundle.json.gz"
TREE = "tree"
TAR_TYPE = "application/x-tar"
MODES = ("snapshot", "checks", "apply")
DETAIL_MAX = 4000
MAX_MEMBERS = 200_000
STOP_WAIT_SECONDS = 300

#: Error code -> HTTP status.
ERROR_STATUS = {
    "config_invalid": 422, "path_escape": 422, "plugin_import_failed": 422,
    "registry_error": 422, "timeout": 504, "too_large": 413, "bad_request": 400,
    "busy": 409, "internal": 500,
}

#: Record keys the hosted store adds that are not part of a decision.
INTERNAL_KEYS = ("rev", "vote_ids", "stale", "stale_reason", "stale_new_sid")

#: Paths and names that hold secrets in the worker and API containers.
SECRET_PATHS = ("/run/secrets", "/run/secrets/curate_env")
SECRET_ENV_PREFIXES = ("POSTGRES_", "CWB_DATABASE", "CWB_GITHUB", "JWT_", "SESSION_SECRET",
                       "OIDC_", "SNAPGATE_")
#: Outbound connections the self-check tries; each must fail in production.
PROBE_TARGETS = (("example.com", 443), ("1.1.1.1", 443), ("github.com", 443),
                 ("postgres", 5432), ("curate", 8000))
PROBE_TIMEOUT = 3.0


def max_tree_bytes() -> int:
    return int(os.environ.get("CWB_MAX_TREE_BYTES") or 200 * 2**20)


def run_timeout() -> int:
    return int(os.environ.get("CWB_RUNNER_TIMEOUT") or 300)


class RunError(Exception):
    """A job that failed in a way the worker should record; `code` is a key of ERROR_STATUS."""

    def __init__(self, code: str, message: str, detail: str = ""):
        if code not in ERROR_STATUS:
            raise ValueError(f"unknown runner error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = (detail or "")[-DETAIL_MAX:]

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


# -- the tree wire format ---------------------------------------------------------

#: One tar member: kind is "file" (data: bytes), "dir" (data: None) or
#: "symlink" (data: the link target, relative, staying inside the tree).
Member = namedtuple("Member", "name kind data")


def safe_name(name: str) -> str:
    """A relative POSIX path with no ``..``, empty, ``.`` or absolute parts."""
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        raise RunError("bad_request", f"unsafe path in archive: {name!r}")
    parts = PurePosixPath(name).parts
    if not parts or any(p in ("", ".", "..") for p in parts):
        raise RunError("bad_request", f"unsafe path in archive: {name!r}")
    return "/".join(parts)


def link_stays_inside(name: str, target: str) -> bool:
    """Whether symlink `name` -> `target` resolves inside the tree it sits in."""
    if not target or target.startswith("/") or "\x00" in target:
        return False
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
    return joined != ".." and not joined.startswith("../") and not joined.startswith("/")


def read_members(data: bytes, *, max_bytes: int, max_members: int = MAX_MEMBERS,
                 code: str = "bad_request") -> list[Member]:
    """Every member of an uncompressed tar, checked. Refuses devices, hard
    links, symlinks out of the tree, absolute paths, ``..``, duplicates, and
    archives over the limits (`too_large`)."""
    out, seen, total = [], set(), 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
            for count, m in enumerate(tar, start=1):
                if count > max_members:
                    raise RunError("too_large", f"archive has more than {max_members} entries")
                name = safe_name(m.name.removesuffix("/"))
                if name in seen:
                    raise RunError(code, f"{name} appears twice in the archive")
                seen.add(name)
                if m.isdir():
                    out.append(Member(name, "dir", None))
                elif m.issym():
                    if not link_stays_inside(name, m.linkname):
                        raise RunError("path_escape", f"{name} is a symlink that leaves the tree")
                    out.append(Member(name, "symlink", m.linkname))
                elif m.isreg():
                    total += m.size
                    if total > max_bytes:
                        raise RunError("too_large", f"archive content is larger than {max_bytes} bytes")
                    fh = tar.extractfile(m)
                    out.append(Member(name, "file", fh.read() if fh is not None else b""))
                else:
                    raise RunError(code, f"{name} is a hard link or special file")
    except tarfile.TarError as e:
        raise RunError(code, f"not a readable tar archive: {e}") from None
    return out


def write_members(members) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:", format=tarfile.PAX_FORMAT) as tar:
        for m in members:
            info = tarfile.TarInfo(safe_name(m.name))
            if m.kind == "dir":
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                tar.addfile(info)
            elif m.kind == "symlink":
                info.type, info.linkname, info.mode = tarfile.SYMTYPE, m.data, 0o777
                tar.addfile(info)
            else:
                info.size, info.mode = len(m.data), 0o644
                tar.addfile(info, io.BytesIO(m.data))
    return buf.getvalue()


def pack_job(job: dict, members) -> bytes:
    """The ``POST /run`` body: ``job.json`` plus the tree's members under ``tree/``."""
    tree = [Member(f"{TREE}/{m.name}", m.kind, m.data) for m in members]
    return write_members([Member(JOB_JSON, "file", json.dumps(job).encode()), *tree])


def check_job(job) -> dict:
    if not isinstance(job, dict):
        raise RunError("bad_request", f"{JOB_JSON} must be an object")
    unknown = sorted(set(job) - {"mode", "config_path", "plugins_enabled", "decisions"})
    if unknown:
        raise RunError("bad_request", f"{JOB_JSON} has unknown field(s): {', '.join(unknown)}")
    if job.get("mode") not in MODES:
        raise RunError("bad_request", f"mode must be one of {list(MODES)}")
    if job["mode"] == "apply":
        raise RunError("bad_request", "mode apply is reserved and not available yet")
    safe_name(job.get("config_path") or "")
    if not isinstance(job.get("plugins_enabled"), bool):
        raise RunError("bad_request", "plugins_enabled must be true or false")
    if not isinstance(job.get("decisions", []), list):
        raise RunError("bad_request", "decisions must be a list")
    return job


def unpack_job(data: bytes, jobdir: Path) -> dict:
    """Check a ``POST /run`` body and write it into ``jobdir`` (``job.json``, ``tree/``)."""
    members = read_members(data, max_bytes=max_tree_bytes() + 64 * 2**20)
    files = {m.name: m for m in members}
    if JOB_JSON not in files or files[JOB_JSON].kind != "file":
        raise RunError("bad_request", f"the archive has no {JOB_JSON}")
    try:
        job = json.loads(files.pop(JOB_JSON).data)
    except ValueError as e:
        raise RunError("bad_request", f"{JOB_JSON} is not JSON: {e}") from None
    check_job(job)
    tree = Path(jobdir) / TREE
    tree.mkdir(parents=True)
    links = []
    for m in members:
        if m.name == JOB_JSON:
            continue
        head, _, rel = m.name.partition("/")
        if head != TREE:
            raise RunError("bad_request", f"unexpected file {m.name} in the archive")
        if not rel:
            continue
        dest = tree / rel
        if m.kind == "dir":
            dest.mkdir(parents=True, exist_ok=True)
        elif m.kind == "symlink":
            if not link_stays_inside(rel, m.data):
                raise RunError("path_escape", f"{rel} is a symlink that leaves the tree")
            links.append((dest, m.data))
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(m.data)
    for dest, target in links:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(target)
    (Path(jobdir) / JOB_JSON).write_text(json.dumps(job))
    return job


# -- one job, in the child process ------------------------------------------------


def _committed_output_warnings(wb, bundle) -> list[str]:
    """Generated files committed in the repo that differ from what this commit
    regenerates (ignoring `_generated_by`)."""
    from . import derive, review, snapshot, status
    files = snapshot.files(wb, bundle)
    st = derive.status(bundle, bundle["repo_decisions"])
    files[wb.cfg.output("status.json")] = status.dump(st)
    rv = derive.review(bundle, st)
    if rv is not None:
        files[wb.cfg.output("review.json")] = review.dump(rv)

    def norm(text):
        d = json.loads(text)
        if isinstance(d, dict):
            d.pop("_generated_by", None)
        return d

    stale = []
    for path, text in files.items():
        if not path.is_file():
            continue
        try:
            same = norm(path.read_text()) == norm(text)
        except ValueError:
            same = False
        if not same:
            stale.append(wb.cfg.rel(path))
    return [f"committed outputs are out of date: {', '.join(sorted(stale))}"] if stale else []


def _open(jobdir: Path, job: dict):
    from .workbench import Workbench
    tree = (Path(jobdir) / TREE).resolve()
    config = tree / job["config_path"]
    if not config.resolve().is_relative_to(tree):
        raise RunError("path_escape", f"{job['config_path']} leaves the tree")
    if not config.is_file():
        raise RunError("config_invalid", f"no {job['config_path']} in the tree")
    return Workbench.open(config, root=tree, hosted=True, plugins=job["plugins_enabled"])


def _snapshot(jobdir: Path, job: dict) -> dict:
    from . import snapshot
    wb = _open(jobdir, job)
    bundle = snapshot.build(wb)
    bundle["warnings"] += _committed_output_warnings(wb, bundle)
    return bundle


def _checks(jobdir: Path, job: dict) -> dict:
    from . import snapshot, store, subjects
    wb = _open(jobdir, job)
    doc = snapshot.plain(subjects.document(wb)[0])
    subj = store.Subjects(doc)
    verdicts = store.load_verdicts(wb.cfg.verdicts)
    decisions = job.get("decisions") or []
    known = {d.get("id") for d in decisions} | {r["id"] for r in wb.store.records()}
    out = {}
    for rec in decisions:
        rec = store.normalize({k: v for k, v in rec.items() if k not in INTERNAL_KEYS})
        try:
            store.validate(rec, subj, verdicts, known_ids=known, check_hashes=False, wb=wb)
            out[str(rec.get("id"))] = []
        except store.StoreError as e:
            out[str(rec.get("id"))] = list(e.errors)
    return {"checks": out}


def classify(exc: BaseException) -> RunError:
    """Map an exception from a job to a structured error."""
    from .config import ConfigError
    from .review import ReviewTypesError
    from .subjects import RegistryError
    from .workbench import PluginError
    detail = traceback.format_exc()
    if isinstance(exc, RunError):
        return exc
    if isinstance(exc, ConfigError):
        return RunError(exc.code, exc.message, detail)
    if isinstance(exc, PluginError):
        return RunError("plugin_import_failed", str(exc), detail)
    if isinstance(exc, RegistryError):
        return RunError("registry_error", str(exc), detail)
    if isinstance(exc, ReviewTypesError):
        return RunError("config_invalid", f"review types: {exc}", detail)
    return RunError("internal", f"{type(exc).__name__}: {exc}", detail)


def run_job_dir(jobdir: Path) -> dict:
    """Run the job in ``jobdir``; return the result (what ``--job`` writes to result.json).

    ``{"ok": true, "bundle": BUNDLE_FILE}`` (snapshot; the bundle is written
    next to it), ``{"ok": true, "checks": {...}}``, or ``{"ok": false, "error": {...}}``.
    """
    jobdir = Path(jobdir)
    try:
        job = check_job(json.loads((jobdir / JOB_JSON).read_text()))
        if job["mode"] == "snapshot":
            bundle = _snapshot(jobdir, job)
            (jobdir / BUNDLE_FILE).write_bytes(
                gzip.compress(json.dumps(bundle, ensure_ascii=False).encode()))
            return {"ok": True, "bundle": BUNDLE_FILE}
        return {"ok": True, **_checks(jobdir, job)}
    except Exception as e:  # noqa: BLE001 - every failure becomes a structured error
        return {"ok": False, "error": classify(e).to_dict()}


def _child_main(jobdir: Path) -> int:
    result = run_job_dir(jobdir)
    (Path(jobdir) / RESULT_JSON).write_text(json.dumps(result))
    return 0


def subprocess_runner(jobdir: Path, timeout_s: int) -> dict:
    """Run ``python -m curation_workbench.runner --job <jobdir>``; kill it at ``timeout_s``."""
    jobdir = Path(jobdir)
    tmp = jobdir / "tmp"
    tmp.mkdir(exist_ok=True)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp), "TMPDIR": str(tmp),
           "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
    with open(jobdir / "child.log", "wb") as log:
        proc = subprocess.Popen([sys.executable, "-m", "curation_workbench.runner", "--job",
                                 str(jobdir)], cwd=tmp, env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                close_fds=True)
        try:
            proc.wait(timeout=timeout_s)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
    if timed_out:
        return {"ok": False, "error": RunError("timeout", f"the job took longer than {timeout_s} s").to_dict()}
    try:
        return json.loads((jobdir / RESULT_JSON).read_text())
    except (OSError, ValueError):
        tail = (jobdir / "child.log").read_bytes()[-DETAIL_MAX:].decode("utf-8", "replace")
        return {"ok": False, "error": RunError(
            "internal", f"the job process exited {proc.returncode}", tail).to_dict()}


def in_process_runner(jobdir: Path, timeout_s: int) -> dict:
    """Run the job in this process (tests only: no isolation, no timeout)."""
    return run_job_dir(jobdir)


def execute(body: bytes, runner=subprocess_runner, timeout_s: int | None = None):
    """Unpack and run one ``POST /run`` body. Returns ``("bundle", gzip bytes)``
    or ``("checks", dict)``; raises RunError."""
    jobdir = Path(tempfile.mkdtemp(prefix="cwb-run-"))
    try:
        unpack_job(body, jobdir)
        result = runner(jobdir, timeout_s or run_timeout())
        if not result.get("ok"):
            err = result.get("error") or {}
            code = err.get("code") if err.get("code") in ERROR_STATUS else "internal"
            raise RunError(code, err.get("message") or "the job failed", err.get("detail") or "")
        if "bundle" in result:
            return "bundle", (jobdir / result["bundle"]).read_bytes()
        return "checks", result["checks"]
    finally:
        shutil.rmtree(jobdir, ignore_errors=True)


# -- self-check -------------------------------------------------------------------


def _probe_connect(host: str, port: int) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT):
            return False, "CONNECTED"
    except OSError as e:
        return True, f"blocked ({type(e).__name__}: {e})"


def self_check() -> dict:
    """What this process can reach. ``isolated`` is true only if every check passes."""
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": ok, "detail": detail})

    for path in SECRET_PATHS:
        exists = os.path.lexists(path)
        add(f"no {path}", not exists, "present" if exists else "absent")
    names = sorted(n for n in os.environ if n.startswith(SECRET_ENV_PREFIXES))
    add("no secret-like environment variables", not names, ", ".join(names) or "none")
    for host, port in PROBE_TARGETS:
        ok, detail = _probe_connect(host, port)
        add(f"no connection to {host}:{port}", ok, detail)
    probe = Path(sys.prefix) / ".cwb-write-probe"
    try:
        probe.write_bytes(b"x")
        probe.unlink()
        add("read-only root", False, f"wrote {probe}")
    except OSError as e:
        add("read-only root", True, f"write refused ({type(e).__name__})")
    add("not root", os.getuid() != 0, f"uid {os.getuid()}")
    return {"isolated": all(c["ok"] for c in checks), "checks": checks}


# -- the HTTP server --------------------------------------------------------------


class RunnerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, *, runner=None, timeout_s=None, max_body=None):
        super().__init__(address, RunnerHandler)
        self.runner = runner or subprocess_runner
        self.timeout_s = timeout_s
        self.max_body = max_body or max_tree_bytes() + 64 * 2**20
        self.run_lock = threading.Lock()


class RunnerHandler(BaseHTTPRequestHandler):
    server: RunnerServer
    server_version = "cwb-runner"
    sys_version = ""

    def log_message(self, fmt, *args):
        logger.info("%s %s", self.address_string(), fmt % args)

    def _send(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, data):
        self._send(status, json.dumps(data).encode(), "application/json")

    def _error(self, err: RunError):
        self._json(ERROR_STATUS[err.code], {"error": err.to_dict()})

    def do_GET(self):  # noqa: N802 - http.server API
        if self.path == "/health":
            self._json(200, {"status": "ok", "busy": self.server.run_lock.locked()})
        elif self.path == "/self-check":
            self._json(200, self_check())
        else:
            self._error(RunError("bad_request", "not found"))

    def do_POST(self):  # noqa: N802 - http.server API
        if self.path != "/run":
            self._error(RunError("bad_request", "not found"))
            return
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            self._error(RunError("bad_request", "Content-Length is required"))
            return
        if length < 0 or length > self.server.max_body:
            self.close_connection = True
            self._error(RunError("too_large", f"the body is larger than {self.server.max_body} bytes"))
            return
        if not self.server.run_lock.acquire(blocking=False):
            self.rfile.read(length)
            self._error(RunError("busy", "busy: another job is running"))
            return
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                self._error(RunError("bad_request", "the body ended early"))
                return
            try:
                kind, out = execute(body, self.server.runner, self.server.timeout_s)
            except RunError as e:
                self._error(e)
                return
            except Exception as e:  # noqa: BLE001 - always answer
                logger.exception("run request failed")
                self._error(RunError("internal", f"{type(e).__name__}: {e}", traceback.format_exc()))
                return
            if kind == "bundle":
                self._send(200, out, "application/gzip")
            else:
                self._json(200, {"checks": out})
        finally:
            self.server.run_lock.release()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cwb runner", description="Run the isolated runner.")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--job", type=Path, default=None, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args.job is not None:
        return _child_main(args.job)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    server = RunnerServer((args.host, args.port))
    stop = threading.Event()

    def _stop(*_):
        if not stop.is_set():
            stop.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
    logger.info("cwb runner listening on %s:%s", args.host, args.port)
    try:
        server.serve_forever()
    finally:
        if server.run_lock.acquire(timeout=STOP_WAIT_SECONDS):
            server.run_lock.release()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
