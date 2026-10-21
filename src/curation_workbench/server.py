"""Local curation daemon: lets a review page write decisions to the working tree.

Stdlib `http.server`, bound to a loopback address only (it refuses to start on
anything else; there is no auth and there must never be a reason for any).
A published, static site probes `/api/health`, fails, and renders read-only.

Endpoints
---------
  GET    /api/health              root, branch, fingerprint, decision count, dirty flag
  GET    /api/decisions           the whole store (+ mtime_ns)
  POST   /api/decisions           create; mints DEC-NNNN              -> 201
  PUT    /api/decisions/<id>      replace a pending record
  DELETE /api/decisions/<id>      withdraw (status: withdrawn; never removed)
  GET    /api/subjects            subjects.json (from the cached snapshot bundle)
  GET    /api/status              status.json, derived now from the cached bundle and
                                  the current decisions (snapshot.py + derive.py)
  GET    /api/review_types        review_types.yaml as JSON (404 if the project has none)
  POST   /api/apply               run `cwb apply` (streams its output)
  GET    /api/apply               the last run's log: {running, dry_run, log, exit}

Plugins add routes with `wb.routes.append((method, prefix, fn))`, where
fn(api, tail) handles the request (a prefix ending in "/" passes the rest of
the path as `tail`); `api.workbench` is the Workbench, and `api.json_body()`,
`api.send(code, body)` and `store.StoreError` work as in the builtin routes.

The daemon keeps one snapshot bundle (snapshot.build) in memory and rebuilds
it only when an input file's mtime changes (schema modules, SSSOM files, the
term ledger, policy, verdicts, review types, findings files). A decision write
does not rebuild it; status is re-derived from it on every request.

Every decision write goes through the workbench's store (`wb.store`, a
YamlStore here; `store.validate` is the one gate). Errors are
`{"errors": [...]}` with 400 (malformed), 404 (unknown id) or 409 (conflict).
Optimistic concurrency: a mutating request may send the `X-Store-Mtime` header
with the `mtime_ns` it last read; the store compares it under its lock and, if
the file changed since, the daemon answers 409 with the current store.

The server sets `decided_by` (git config user.name), `decided_on`,
`schema_fingerprint`, `status: pending` and `origin: local`; the browser
cannot supply them. Allowed browser origins: option `allowed_origins`
(default http://localhost:4321-4329 and the 127.0.0.1 equivalents).
"""
import contextlib
import datetime
import ipaddress
import json
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import yaml

from . import derive, snapshot, store
from . import status as status_mod
from . import subjects as subjects_mod
from .store.yaml_store import LOCK

SERVER_FIELDS = ("id", "decided_by", "decided_on", "schema_fingerprint", "status", "applied", "origin")
DEFAULT_ORIGINS = [f"http://{h}:{p}" for h in ("localhost", "127.0.0.1") for p in range(4321, 4330)]


def is_loopback(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_loopback for i in infos)


def make_handler(wb, decided_by):
    cfg = wb.cfg
    origins = set(wb.option("allowed_origins", DEFAULT_ORIGINS))

    def git(*args):
        out = subprocess.run(["git", "-C", str(cfg.root), *args], capture_output=True, text=True)
        return out.stdout.strip()

    cache = {"key": None, "bundle": None}
    cache_lock = threading.Lock()

    def input_key():
        paths = [*cfg.schema_dir.glob("*.yaml"), *cfg.sssom_files(), cfg.ledger, cfg.policy,
                 cfg.verdicts, cfg.review_types, *cfg.findings_dir.glob("*.json")]
        return tuple(sorted((str(p), p.stat().st_mtime_ns) for p in paths if p.exists()))

    def bundle():
        """The project's snapshot bundle, rebuilt when an input file changed."""
        with cache_lock:
            key = input_key()
            if cache["key"] != key:
                cache["bundle"] = snapshot.build(wb)
                cache["key"] = key
            return cache["bundle"]

    def subjects():
        return store.Subjects(bundle()["subjects"])

    def verdicts():
        return store.load_verdicts(cfg.verdicts)

    def context(subj):
        return store.ValidationContext(subjects=subj, verdicts=verdicts(), wb=wb)

    class Api(BaseHTTPRequestHandler):
        server_version = "curation-workbench/1"
        workbench = wb
        last_apply = {"running": False, "dry_run": False, "log": [], "exit": None}

        # -------------------------------------------------------- plumbing

        def log_message(self, fmt, *args):
            sys.stderr.write(f"[cwb] {self.command} {self.path} {fmt % args}\n")

        def _cors(self):
            origin = self.headers.get("Origin")
            if origin in origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Expose-Headers", "X-Store-Mtime")

        def send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, indent=1).encode()
            self.send_response(code)
            self._cors()
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            if cfg.decisions.exists():
                self.send_header("X-Store-Mtime", wb.store.token())
            self.end_headers()
            self.wfile.write(data)

        def error(self, code, errors, **extra):
            self.send(code, {"errors": list(errors), **extra})

        def json_body(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError as e:
                raise store.StoreError(400, [f"body is not JSON: {e}"])
            if not isinstance(body, dict):
                raise store.StoreError(400, ["body must be a JSON object"])
            return body

        def _guard(self):
            """Refuse requests from foreign pages and DNS-rebound hosts."""
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            if host not in ("localhost", "127.0.0.1", "::1"):
                self.error(403, [f"bad Host {host!r}"])
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in origins:
                self.error(403, [f"origin {origin} not allowed"])
                return False
            return True

        def _token(self):
            return self.headers.get("X-Store-Mtime")

        def _store_json(self):
            return wb.store.snapshot()

        def _dispatch(self, routes):
            if not self._guard():
                return
            routes = routes + [(p, (lambda f: lambda tail: f(self, tail))(fn))
                               for m, p, fn in wb.routes if m == self.command]
            path = urlparse(self.path).path.rstrip("/")
            for prefix, fn in routes:
                if path == prefix or (prefix.endswith("/") and path.startswith(prefix)):
                    try:
                        with LOCK if self.command != "GET" else contextlib.nullcontext():
                            return fn(path[len(prefix):] if prefix.endswith("/") else None)
                    except store.StoreError as e:
                        extra = {"store": self._store_json()} if e.status == 409 else {}
                        return self.error(e.status, e.errors, **extra)
                    except FileNotFoundError as e:
                        return self.error(503, [f"missing input: {e.filename}"])
                    except subjects_mod.RegistryError as e:
                        return self.error(503, [f"subject registry failed: {e}"])
            self.error(404, [f"no route {self.command} {path}"])

        # -------------------------------------------------------- verbs

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Store-Mtime")
            self.send_header("Access-Control-Max-Age", "600")
            self.end_headers()

        def do_GET(self):
            self._dispatch([
                ("/api/health", self.health),
                ("/api/decisions", lambda _: self.send(200, self._store_json())),
                ("/api/subjects", lambda _: self.send(
                    200, subjects_mod.dump_json(bundle()["subjects"]).encode())),
                ("/api/status", lambda _: self.send(200, status_mod.dump(
                    derive.status(bundle(), wb.store.records())).encode())),
                ("/api/review_types", self.review_types),
                ("/api/apply", lambda _: self.send(200, Api.last_apply)),
            ])

        def do_POST(self):
            self._dispatch([("/api/decisions", self.create), ("/api/apply", self.apply)])

        def do_PUT(self):
            self._dispatch([("/api/decisions/", self.update)])

        def do_DELETE(self):
            self._dispatch([("/api/decisions/", self.withdraw)])

        def do_PATCH(self):
            self._dispatch([])

        # -------------------------------------------------------- handlers

        def health(self, _):
            self.send(200, {
                "ok": True,
                "project": cfg.name,
                "repo_root": str(cfg.root),
                "git_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
                "schema_fingerprint": subjects().fingerprint,
                "decision_count": len(wb.store.records()),
                "working_tree_dirty": bool(git("status", "--porcelain")),
                "decided_by": decided_by,
                "apply_available": True,
            })

        def review_types(self, _):
            if not cfg.review_types.exists():
                return self.error(404, [f"{cfg.rel(cfg.review_types)} does not exist"])
            self.send(200, yaml.safe_load(cfg.review_types.read_text()))

        def _client_record(self):
            body = self.json_body()
            body.pop("mtime_ns", None)
            for f in SERVER_FIELDS:
                body.pop(f, None)
            return body

        def create(self, _):
            rec = self._client_record()
            rec["decided_by"] = decided_by
            rec["origin"] = {"kind": "local", "ref": None}
            subj = subjects()
            rec["schema_fingerprint"] = subj.fingerprint
            out = wb.store.create(rec, context(subj), expect_token=self._token())
            self.send(201, {"decision": out})

        def update(self, dec_id):
            cur = wb.store.get(dec_id)
            rec = self._client_record()
            subj = subjects()
            rec.update(id=dec_id, decided_by=decided_by, status="pending",
                       decided_on=datetime.date.today().isoformat(),
                       origin=cur["origin"], schema_fingerprint=subj.fingerprint)
            out = wb.store.update(dec_id, rec, context(subj), expect_token=self._token())
            self.send(200, {"decision": out})

        def withdraw(self, dec_id):
            self.send(200, {"decision": wb.store.withdraw(dec_id, expect_token=self._token())})

        def apply(self, _):
            body = self.json_body()
            cmd = [sys.executable, "-m", "curation_workbench.cli", "-c", str(cfg.path),
                   "--root", str(cfg.root), "--out", str(cfg.out), "apply"]
            if body.get("dry_run"):
                cmd.append("--dry-run")
            if body.get("ids"):
                cmd += ["--ids", *map(str, body["ids"])]
            proc = subprocess.Popen(cmd, cwd=cfg.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            run = Api.last_apply = {"running": True, "dry_run": bool(body.get("dry_run")),
                                    "log": [], "exit": None}
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Connection", "close")
            self.end_headers()
            client = True
            for line in proc.stdout:
                run["log"].append(line.decode(errors="replace").rstrip("\n"))
                if client:
                    try:
                        self.wfile.write(line)
                        self.wfile.flush()
                    except OSError:
                        client = False
            run["exit"], run["running"] = proc.wait(), False
            if client:
                try:
                    self.wfile.write(f"\nexit {run['exit']}\n".encode())
                except OSError:
                    pass
            self.close_connection = True

    return Api


def serve(wb, host="127.0.0.1", port=8781):
    if not is_loopback(host):
        sys.exit(f"refusing to bind {host!r}: the curation daemon listens on loopback only")
    out = subprocess.run(["git", "-C", str(wb.cfg.root), "config", "user.name"],
                         capture_output=True, text=True)
    decided_by = out.stdout.strip()
    if not decided_by:
        sys.exit("git config user.name is empty; the daemon needs it for decided_by")
    ThreadingHTTPServer.address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
    httpd = ThreadingHTTPServer((host, port), make_handler(wb, decided_by))
    print(f"curation daemon for {wb.cfg.name} on http://{host}:{port} (decided_by={decided_by})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return httpd
