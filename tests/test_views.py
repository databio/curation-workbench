"""The review UI's read routes on the toy project: each `views` function gives
the body the daemon route returns, the SPA is served with its deep-link
fallback, and the old routes are unchanged. Runs on a temp copy of the toy
with a stand-in built UI; the daemon listens on a spare port (never 8781)."""
import json
import shutil
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from curation_workbench import derive, review, server, snapshot, views
from curation_workbench.workbench import Workbench

TOY = Path(__file__).parent / "fixtures" / "toy"
PORT = 18791


@pytest.fixture
def daemon(tmp_path):
    root = tmp_path / "toy"
    shutil.copytree(TOY, root)
    ui = tmp_path / "ui"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<!doctype html><div id=root></div>")
    (ui / "assets" / "app.js").write_text("console.log(1)")
    wb = Workbench.open(root / "workbench.yaml")
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), server.make_handler(wb, "tester", ui))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield wb
    finally:
        httpd.shutdown()
        httpd.server_close()


def call(path, method="GET", body=None, accept="application/json"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method)
    req.add_header("Accept", accept)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def get(path):
    code, _, body = call(path)
    assert code == 200, (path, body[:300])
    return json.loads(body)


def test_views_equal_routes(daemon):
    wb = daemon
    rec = {"subject": "term:TOY_0001", "verdict": "keep", "rationale": "fine",
           "subject_hashes": {"term:TOY_0001": snapshot.build(wb)["subjects"]["subjects"]
                              ["term:TOY_0001"]["hash"]}}
    code, _, body = call("/api/decisions", "POST", rec)
    assert code == 201, body
    bundle = snapshot.build(wb)
    records = wb.store.records()
    st = derive.status(bundle, records)
    rv = derive.review(bundle, st, review.next_id(wb.store))
    assert get("/api/config") == {"mode": "local", "projects": [{"slug": "toy", "title": "Toy"}],
                                  "user": {"name": "tester"}}
    assert get("/api/review") == views.review_index(rv)
    assert all("items" not in ty for t in get("/api/review")["tracks"] for ty in t["types"])
    page = get("/api/review/schema/fields")
    assert page == views.review_type_page(rv, "schema", "fields")
    assert set(page) == {"track", "type", "title", "decisions_next_id", "items"}
    assert page["decisions_next_id"] == "DEC-0002"
    assert get("/api/subject_index") == views.subject_index(bundle, st)
    sub = get("/api/subject?sid=term:TOY_0001")
    assert sub == json.loads(json.dumps(views.subject_view(bundle, st, records, "term:TOY_0001")))
    assert set(sub) == {"subject", "status", "findings", "decisions", "aliases", "prompts", "panels", "browse"}
    assert [d["id"] for d in sub["decisions"]] == ["DEC-0001"]
    assert get("/api/verdicts") == views.verdicts_view(bundle["verdicts"])
    assert get("/api/findings") == json.loads(json.dumps(views.findings_view(bundle, st)))
    assert get("/api/history") == json.loads(json.dumps(views.history(records, bundle)))
    pend = get("/api/pending")
    assert [d["id"] for d in pend["decisions"]] == ["DEC-0001"] and pend["ok"] and pend["errors"] == {}
    assert isinstance(pend["files"], list)


def test_not_found(daemon):
    assert call("/api/review/schema/nope")[0] == 404
    assert call("/api/review/schema")[0] == 404
    assert call("/api/subject?sid=term:NOPE")[0] == 404
    code, headers, body = call("/api/nope")
    assert code == 404 and headers["Content-Type"] == "application/json" and json.loads(body)["errors"]


def test_spa_fallback(daemon):
    code, headers, body = call("/p/toy/review/schema/fields", accept="text/html,*/*")
    assert code == 200 and headers["Content-Type"].startswith("text/html") and b"id=root" in body
    code, headers, body = call("/assets/app.js", accept="*/*")
    assert code == 200 and "immutable" in headers["Cache-Control"] and body == b"console.log(1)"
    assert call("/assets/missing.js", accept="text/html")[0] == 404
    assert call("/p/toy", accept="application/json")[0] == 404
    assert call("/../../etc/passwd", accept="*/*")[0] == 404


def test_store_routes(daemon):
    assert get("/api/health")["project"] == "toy"
    assert get("/api/decisions")["decisions"] == []
    for gone in ("/api/subjects", "/api/status", "/api/review_types"):
        assert call(gone)[0] == 404, gone


def test_foreign_origin_refused(daemon):
    """No CORS: the review UI is same-origin, so any other Origin is refused."""
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/decisions", method="GET")
    req.add_header("Origin", "http://localhost:4321")
    try:
        urllib.request.urlopen(req)
        raise AssertionError("foreign origin was served")
    except urllib.error.HTTPError as e:
        assert e.code == 403 and "Access-Control-Allow-Origin" not in e.headers
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/decisions", method="GET")
    req.add_header("Origin", f"http://127.0.0.1:{PORT}")
    with urllib.request.urlopen(req) as r:
        assert r.status == 200 and "Access-Control-Allow-Origin" not in r.headers


def test_no_ui_serves_api_only(tmp_path):
    root = tmp_path / "toy"
    shutil.copytree(TOY, root)
    wb = Workbench.open(root / "workbench.yaml")
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT + 1), server.make_handler(wb, "t", tmp_path / "none"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT + 1}/p/toy", headers={"Accept": "text/html"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()

