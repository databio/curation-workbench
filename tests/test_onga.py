"""ONGA, the first resident project, through its own workbench.yaml.

ONGA's committed outputs are the golden files: `cwb subjects|lint|status|review
--check` must find them up to date (ONGA's `make test` runs the same checks).
Read-only on ONGA_ROOT; the apply dry run and the daemon routes use
ONGA_SCRATCH (a disposable ONGA worktree).
"""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from curation_workbench import apply, cli, server, store
from curation_workbench.workbench import Workbench


@pytest.mark.parametrize("cmd", ["subjects", "lint", "status", "review"])
def test_committed_outputs_are_current(onga, cmd, capsys):
    assert cli.main(["-c", str(onga / "workbench.yaml"), cmd, "--check"]) == 0, capsys.readouterr().out


def test_decisions_validate(onga):
    """Every record normalizes; every one not yet applied validates against the
    current registry (applied ones may name subjects their apply retired)."""
    wb = Workbench.open(onga / "workbench.yaml")
    recs = wb.store.records()
    assert recs
    subjects = store.Subjects.load(wb.cfg.output("subjects.json"))
    verdicts = store.load_verdicts(wb.cfg.verdicts)
    known = {r["id"] for r in recs}
    for r in recs:
        rec = store.normalize(r)
        if rec["status"] != "applied":
            store.validate(rec, subjects, verdicts, known_ids=known, check_hashes=False, wb=wb)


def test_plugin_loaded_from_plugin_dir(onga):
    wb = Workbench.open(onga / "workbench.yaml")
    assert wb.cfg.plugin_dir == (onga / "curation" / "plugin").resolve()
    assert any(p == "/api/proposals/" for _, p, _ in wb.routes)


def test_apply_dry_run(scratch, tmp_path):
    wb = Workbench.open(scratch / "workbench.yaml")
    emit = tmp_path / "pending.json"
    assert apply.main(wb, dry_run=True, emit_path=emit) == 0
    data = json.loads(emit.read_text())
    pending = [r["id"] for r in wb.store.records() if r["status"] == "pending"]
    assert sorted(d["id"] for d in data["decisions"]) == sorted(pending)


def call(port, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_review_ui_routes(scratch, tmp_path):
    """The review UI's read routes on ONGA: status codes and body keys."""
    port = 18783
    wb = Workbench.open(scratch / "workbench.yaml")
    httpd = ThreadingHTTPServer(("127.0.0.1", port), server.make_handler(wb, "tester", tmp_path / "no-ui"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def get(path):
        code, body = call(port, path)
        assert code == 200, (path, body[:300])
        return json.loads(body)
    try:
        assert get("/api/config")["projects"] == [{"slug": "onga", "title": "ONGA"}]
        rv = get("/api/review")
        assert rv["version"] == 2 and rv["more_links"]
        committed = json.loads((scratch / "curation" / "review.json").read_text())
        assert rv["progress"]["waiting"] == sum(t["total"] for t in committed["tracks"])
        links = next(ty for t in committed["tracks"] if t["id"] == "vocabulary"
                     for ty in t["types"] if ty["id"] == "links")
        page = get("/api/review/vocabulary/links")
        assert len(page["items"]) == links["count"] and page["type"]["answers_kind"] == "links"
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
        assert call(port, "/api/review/vocabulary/nope")[0] == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
