"""The worker's side of the runner: send a tree, get a bundle or decision checks.

`RemoteRunner(url)` posts to the isolated `cwb runner` (production:
`CWB_RUNNER_URL=http://curate-runner:8150`). `LocalRunner` runs the same job
code in a child process on this machine (dev and tests, `CWB_RUNNER_URL`
unset); a production worker refuses to start without the URL
(`settings.worker_violations`).

Both raise `RunnerError` with the runner's error code (config_invalid,
path_escape, plugin_import_failed, registry_error, timeout, too_large,
bad_request, internal) or a transport code (runner_unreachable, runner_busy,
runner_timeout, runner_bad_response).
"""
from __future__ import annotations

import gzip
import json
import socket
import urllib.error
import urllib.request

from ... import runner as wire

TRANSPORT_CODES = ("runner_unreachable", "runner_busy", "runner_timeout", "runner_bad_response")


class RunnerError(Exception):
    def __init__(self, code, message, detail=""):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.detail = detail or ""


def _job(mode, config_path, plugins_enabled, decisions=None):
    job = {"mode": mode, "config_path": config_path, "plugins_enabled": bool(plugins_enabled)}
    if decisions is not None:
        job["decisions"] = decisions
    return job


def _bundle(gz):
    try:
        return json.loads(gzip.decompress(gz))
    except (OSError, ValueError) as e:
        raise RunnerError("runner_bad_response", f"the bundle is not gzip JSON: {e}") from None


class _Base:
    def snapshot(self, members, config_path, plugins_enabled):
        kind, out = self._run(wire.pack_job(_job("snapshot", config_path, plugins_enabled), members))
        if kind != "bundle":
            raise RunnerError("runner_bad_response", "expected a bundle")
        return _bundle(out)

    def checks(self, members, config_path, plugins_enabled, decisions):
        kind, out = self._run(wire.pack_job(_job("checks", config_path, plugins_enabled,
                                                 decisions), members))
        if kind != "checks":
            raise RunnerError("runner_bad_response", "expected decision checks")
        return out


class LocalRunner(_Base):
    """Runs jobs in a local child process (`python -m curation_workbench.runner --job`)."""

    def __init__(self, timeout_s=300, run_fn=None):
        self.timeout_s = timeout_s
        self.run_fn = run_fn or wire.subprocess_runner

    def _run(self, body):
        try:
            return wire.execute(body, self.run_fn, self.timeout_s)
        except wire.RunError as e:
            raise RunnerError(e.code, e.message, e.detail) from None

    def get_json(self, path, timeout=60):
        if path == "/self-check":
            return wire.self_check()
        return {"status": "ok", "busy": False}


class RemoteRunner(_Base):
    def __init__(self, url, timeout_s=300):
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s

    def _open(self, req, timeout):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            return opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            body = e.read()
            if e.code == 409:
                raise RunnerError("runner_busy", "the runner is busy") from None
            try:
                err = json.loads(body)["error"]
                raise RunnerError(err["code"], err["message"], err.get("detail") or "") from None
            except (ValueError, KeyError, TypeError):
                raise RunnerError("runner_bad_response", f"HTTP {e.code}") from None
        except (socket.timeout, TimeoutError):
            raise RunnerError("runner_timeout", f"no answer within {timeout} s") from None
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise RunnerError("runner_timeout", f"no answer within {timeout} s") from None
            raise RunnerError("runner_unreachable", f"{self.url}: {reason}") from None

    def _run(self, body):
        req = urllib.request.Request(f"{self.url}/run", data=body, method="POST",
                                     headers={"Content-Type": wire.TAR_TYPE})
        with self._open(req, self.timeout_s + 30) as resp:
            ctype = resp.headers.get("Content-Type", "")
            data = resp.read()
        if ctype.startswith("application/gzip"):
            return "bundle", data
        try:
            return "checks", json.loads(data)["checks"]
        except (ValueError, KeyError, TypeError):
            raise RunnerError("runner_bad_response", "unexpected answer from the runner") from None

    def get_json(self, path, timeout=60):
        with self._open(urllib.request.Request(f"{self.url}{path}"), timeout) as resp:
            return json.loads(resp.read())


def make_runner(settings):
    if settings.runner_url:
        return RemoteRunner(settings.runner_url, settings.runner_timeout)
    return LocalRunner(settings.runner_timeout)
