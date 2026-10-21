"""Stage 6: the daemon answers like ONGA's scripts/curation_server.py.

Runs both against ONGA_SCRATCH on spare ports (never 8781), replays one
request sequence on each (restoring decisions.yaml in between) and compares
status codes and JSON bodies (ignoring mtime_ns and the health `project` key).
"""
import json
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

from conftest import ADAPTER

from curation_workbench import server, store
from curation_workbench.workbench import Workbench

OLD_PORT, NEW_PORT = 18781, 18782
ORIGIN = "http://localhost:4321"


def call(port, method, path, body=None, origin=ORIGIN, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    req.add_header("Host", f"127.0.0.1:{port}")
    if origin:
        req.add_header("Origin", origin)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def norm(raw):
    try:
        d = json.loads(raw)
    except ValueError:
        return raw.decode(errors="replace")

    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if k not in ("mtime_ns", "project")}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x
    return strip(d)


def sequence(port, subjects):
    term = next(s for s, r in subjects.by_sid.items() if r["kind"] == "term" and not r["retired"])
    h = subjects.get(term)["hash"]
    good = {"subject": term, "verdict": "keep", "rationale": "fine", "subject_hashes": {term: h},
            "evidence": [{"kind": "finding", "id": "lint:x"}]}
    out = [call(port, "GET", "/api/health"),
           call(port, "GET", "/api/health", origin="http://evil.example"),
           call(port, "GET", "/api/nope"),
           call(port, "POST", "/api/decisions", {**good, "rationale": ""}),
           call(port, "POST", "/api/decisions", {**good, "subject_hashes": {term: "0" * 64}}),
           call(port, "POST", "/api/decisions", good)]
    rid = json.loads(out[-1][1])["decision"]["id"]
    out += [call(port, "PUT", f"/api/decisions/{rid}", {**good, "rationale": "changed"}),
            call(port, "POST", "/api/decisions", good, headers={"X-Store-Mtime": "1"}),
            call(port, "DELETE", f"/api/decisions/{rid}"),
            call(port, "PUT", f"/api/decisions/{rid}", good),
            call(port, "DELETE", "/api/decisions/DEC-9999"),
            call(port, "PATCH", "/api/proposals/NOPE", {"status": "filed"}),
            call(port, "PATCH", "/api/proposals/NOPE", {"status": "bogus"}),
            call(port, "GET", "/api/decisions")]
    return [(code, norm(body)) for code, body in out]


def wait(port):
    for _ in range(100):
        try:
            call(port, "GET", "/api/health")
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"no daemon on {port}")


def test_daemon_matches(scratch):
    wb = Workbench.open(ADAPTER, root=scratch)
    path = wb.cfg.decisions
    original = path.read_text()
    subjects = store.Subjects.load(wb.cfg.output("subjects.json"))
    old = subprocess.Popen([sys.executable, "scripts/curation_server.py", "--port", str(OLD_PORT)],
                           cwd=scratch, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait(OLD_PORT)
        a = sequence(OLD_PORT, subjects)
    finally:
        old.terminate()
        old.wait()
        path.write_text(original)
    decided_by = subprocess.run(["git", "-C", str(scratch), "config", "user.name"],
                                capture_output=True, text=True).stdout.strip()
    httpd = ThreadingHTTPServer(("127.0.0.1", NEW_PORT), server.make_handler(wb, decided_by))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        b = sequence(NEW_PORT, subjects)
    finally:
        httpd.shutdown()
        path.write_text(original)
    assert len(a) == len(b)
    for i, (x, y) in enumerate(zip(a, b)):
        assert x == y, (i, x, y)


def test_review_ui_routes(scratch, tmp_path):
    """The review UI's read routes on ONGA: status codes and body keys."""
    port = 18783
    wb = Workbench.open(ADAPTER, root=scratch)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), server.make_handler(wb, "tester", tmp_path / "no-ui"))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    def get(path):
        code, body = call(port, "GET", path, origin=None)
        assert code == 200, (path, body[:300])
        return json.loads(body)
    try:
        assert get("/api/config")["projects"] == [{"slug": "onga", "title": "ONGA"}]
        rv = get("/api/review")
        assert rv["version"] == 2 and rv["more_links"] and rv["progress"]["waiting"] == 396
        page = get("/api/review/vocabulary/links")
        assert len(page["items"]) == 165 and page["type"]["answers_kind"] == "links"
        assert any(c["text"].endswith(")") for i in page["items"] for c in i["choices"])
        idx = get("/api/subject_index")
        slot = next(s for s, r in idx.items() if r["kind"] == "slot" and r["layer"] == 2 and not r["retired"])
        sub = get(f"/api/subject?sid={slot}")
        assert any(p["decide_on"] == "owner_class" for p in sub["prompts"])
        term = get("/api/subject?sid=term:ONGA_0000001")
        assert term["browse"].startswith("https://dev.databio.org/onga/")
        assert any(p["title"] == "Usage" for p in term["panels"])
        assert set(get("/api/verdicts")["term"]) >= {"keep", "adopt_mapping"}
        assert get("/api/findings")["findings"]
        assert get("/api/history")["history"][0]["id"].startswith("DEC-")
        assert set(get("/api/pending")) == {"decisions", "errors", "files", "ok"}
        assert call(port, "GET", "/api/review/vocabulary/nope", origin=None)[0] == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
