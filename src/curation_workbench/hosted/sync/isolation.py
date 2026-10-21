"""`cwb hosted isolation-check`: prove the runner cannot reach secrets or the network.

Run it in the worker task (it reaches the runner at `CWB_RUNNER_URL`); it
needs no database and writes nothing. It:

1. reads the runner's `GET /self-check`: no /run/secrets, no secret-like
   environment variables, every outbound connection refused, a read-only
   root, not root;
2. sends a probe project whose plugin, at load time, tries to read
   /run/secrets/curate_env, open a socket to the internet and write outside
   /tmp, and reports each outcome as a bundle warning (`PROBE name=value`).
   All three must fail.

It prints PASS/FAIL lines and ends `ISOLATED` or `NOT ISOLATED`; the exit
status is 0 only when isolated.
"""
from __future__ import annotations

from ...runner import Member
from .remote import LocalRunner, RemoteRunner, RunnerError

SECRET_PATH = "/run/secrets/curate_env"
MARK = "PROBE "

PROBE_PLUGIN = '''"""Isolation probe: tries what a hostile plugin would, and reports."""
import os
import socket
import sys


def register(wb):
    try:
        with open(%(secret)r) as fh:
            fh.read(1)
        secret = "READ"
    except OSError as e:
        secret = "blocked (" + type(e).__name__ + ")"
    try:
        socket.create_connection(("example.com", 443), timeout=3).close()
        network = "CONNECTED"
    except OSError as e:
        network = "blocked (" + type(e).__name__ + ")"
    wrote = []
    for d in ("/", sys.prefix, "/etc", "/var/tmp"):
        p = os.path.join(d, ".cwb-probe-write")
        try:
            with open(p, "w") as fh:
                fh.write("x")
            os.unlink(p)
            wrote.append(d)
        except OSError:
            pass
    write = ("WROTE " + ", ".join(wrote)) if wrote else "blocked"
    for name, value in (("secret", secret), ("network", network), ("write", write)):
        wb.warnings.append("PROBE " + name + "=" + value)
'''

PROBE_FILES = {
    "workbench.yaml": "project: probe\nschema: {dir: src, root: probe.yaml}\n"
                      "terms: {prefix: probe, local: PROBE_, digits: 4, base: https://example.org/probe/}\n"
                      "plugin_dir: plugin\nplugins: [probe_plugin]\n",
    "src/probe.yaml": "id: https://example.org/probe\nname: probe\nimports:\n  - linkml:types\n"
                      "classes:\n  Thing:\n    description: A class so the registry is not empty.\n",
    "curation/decisions.yaml": "version: 1\nnext_id: 1\ndecisions: []\n",
    "curation/verdicts.yaml": "version: 1\nverdicts: {}\n",
    "curation/policy.yaml": "lint:\n  rules: {}\nqueue:\n  findings: 1.0\n"
                            "status:\n  severity_points: {error: 3, warn: 2, info: 1}\n",
    "plugin/probe_plugin.py": PROBE_PLUGIN % {"secret": SECRET_PATH},
}


def probe_members():
    dirs = sorted({"/".join(p.split("/")[:i]) for p in PROBE_FILES
                   for i in range(1, p.count("/") + 1)})
    return [Member(d, "dir", None) for d in dirs] + \
        [Member(p, "file", t.encode()) for p, t in sorted(PROBE_FILES.items())]


def probe(runner):
    """{name: outcome} from the probe project's plugin, run by `runner`."""
    bundle = runner.snapshot(probe_members(), "workbench.yaml", True)
    out = {}
    for w in bundle.get("warnings") or []:
        if w.startswith(MARK):
            name, _, value = w[len(MARK):].partition("=")
            out[name] = value
    return out


def run_isolation_check(runner_url, *, out=print, runner=None):
    """The whole check; 0 only if the runner is isolated. `runner` overrides
    the client (tests use a LocalRunner, which is not isolated)."""
    if runner is None:
        if not runner_url:
            out("FAIL  CWB_RUNNER_URL is not set; run this in the curate-worker task")
            out("NOT ISOLATED")
            return 1
        runner = RemoteRunner(runner_url)
    ok = True

    def report(passed, label):
        nonlocal ok
        ok = ok and passed
        out(f"{'PASS' if passed else 'FAIL'}  {label}")

    out(f"runner: {runner_url or type(runner).__name__}")
    try:
        result = runner.get_json("/self-check")
        for c in result.get("checks", []):
            report(bool(c.get("ok")), f"self-check: {c.get('name')} ({c.get('detail')})")
        report(bool(result.get("isolated")), "self-check: runner reports isolated")
    except RunnerError as e:
        report(False, f"self-check: {e}")
    try:
        found = probe(runner)
        report(str(found.get("secret", "")).startswith("blocked"),
               f"probe: plugin cannot read {SECRET_PATH} ({found.get('secret')})")
        report(str(found.get("network", "")).startswith("blocked"),
               f"probe: plugin cannot reach the internet ({found.get('network')})")
        report(found.get("write") == "blocked",
               f"probe: plugin cannot write outside /tmp ({found.get('write')})")
    except RunnerError as e:
        report(False, f"probe run failed: {e}")
    out("ISOLATED" if ok else "NOT ISOLATED")
    return 0 if ok else 1


__all__ = ["LocalRunner", "probe", "probe_members", "run_isolation_check"]
