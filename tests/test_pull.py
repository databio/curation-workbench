"""`cwb pull` on the toy project against a stand-in for the hosted site's
export route (a local HTTP server on a spare port). Runs on a temp copy of
the toy, so nothing in the repo changes."""
import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from curation_workbench import cli, pull, store, subjects
from curation_workbench.workbench import Workbench

TOY = Path(__file__).parent / "fixtures" / "toy"
KEY = "cwb_test_key"


@pytest.fixture
def toy(tmp_path):
    root = tmp_path / "toy"
    shutil.copytree(TOY, root)
    wb = Workbench.open(root / "workbench.yaml")
    files, _ = subjects.render(wb)
    for p, text in files.items():
        p.write_text(text)
    return wb


@pytest.fixture
def site():
    """{"body": export body} served at /api/v1/projects/toy/decisions/export."""
    state = {"body": {"decisions": [], "held": [], "commit": "abc123"}, "calls": []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            state["calls"].append((self.path, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != f"Bearer {KEY}":
                code, body = 401, {"detail": "bad key", "code": "invalid_token"}
            elif self.path != "/api/v1/projects/toy/decisions/export":
                code, body = 404, {"detail": "no project", "code": "not_found"}
            else:
                code, body = 200, state["body"]
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield state
    finally:
        httpd.shutdown()
        httpd.server_close()


def hosted(wb, n, sid, verdict, op=None, **kw):
    """A decision as the export route returns it."""
    subj = store.Subjects.load(wb.cfg.output("subjects.json"))
    hid = f"H-{n:04d}"
    rec = {"id": hid, "subject": sid, "verdict": verdict, "rationale": "votes: keep 3",
           "decided_by": "Cura Tor <c@example.org>", "decided_on": "2026-10-21", "status": "pending",
           "operation": {"op": verdict, **op} if op else None,
           "subject_hashes": {sid: subj.get(sid)["hash"]}, "schema_fingerprint": subj.fingerprint,
           "origin": {"kind": "hosted", "ref": hid}, **kw}
    return store.to_plain(store.normalize(rec))


def run_pull(wb, site, tmp_path, monkeypatch, key=KEY):
    monkeypatch.setenv(pull.KEY_ENV, key)
    summary = tmp_path / "summary.md"
    code = cli.main(["-c", str(wb.cfg.path), "pull", "--url", site["url"], "--summary", str(summary)])
    return code, summary.read_text() if summary.exists() else None


def test_pull_applies_and_is_idempotent(toy, site, tmp_path, monkeypatch, capsys):
    site["body"]["decisions"] = [
        hosted(toy, 1, "term:TOY_0002", "edit_description", {"description": "The color of the sky."}),
        hosted(toy, 2, "slot:color", "set_cardinality", {"required": False, "recommended": True}),
        hosted(toy, 3, "term:TOY_0001", "defer"),
    ]
    site["body"]["held"] = [{"id": "H-0004", "reason": "the item changed since it was decided"}]
    code, md = run_pull(toy, site, tmp_path, monkeypatch)
    assert code == 0, capsys.readouterr()
    recs = {r["origin"]["ref"]: r for r in toy.store.records()}
    assert {h: (r["id"], r["status"]) for h, r in recs.items()} == {
        "H-0001": ("DEC-0001", "applied"), "H-0002": ("DEC-0002", "applied"),
        "H-0003": ("DEC-0003", "pending")}              # defer: recorded, not applied
    assert "The color of the sky." in (toy.cfg.schema_dir / "things.yaml").read_text()
    assert "| H-0001 | DEC-0001 |" in md and "Cura Tor |" in md and "c@example.org" not in md
    assert "Recorded only (1)" in md and "H-0004: the item changed" in md
    assert site["calls"][0] == ("/api/v1/projects/toy/decisions/export", f"Bearer {KEY}")
    before = toy.cfg.decisions.read_text()
    code, md = run_pull(toy, site, tmp_path, monkeypatch)
    assert code == 0 and toy.cfg.decisions.read_text() == before
    assert "already in decisions.yaml" in capsys.readouterr().out and "Nothing new to apply." in md


def test_refused_decision_is_left_out(toy, site, tmp_path, monkeypatch):
    good = hosted(toy, 1, "term:TOY_0002", "edit_description", {"description": "Sky."})
    bad = hosted(toy, 2, "term:TOY_0001", "keep")
    bad["subject_hashes"] = {"term:TOY_0001": "0" * 64}        # the item changed since
    orphan = hosted(toy, 3, "slot:color", "keep", supersedes="H-0002")
    site["body"]["decisions"] = [good, bad, orphan]
    code, md = run_pull(toy, site, tmp_path, monkeypatch)
    assert code == 0
    recs = toy.store.records()
    assert [(r["id"], r["origin"]["ref"], r["status"]) for r in recs] == [("DEC-0001", "H-0001", "applied")]
    assert "Left out (2)" in md and "H-0002: " in md and "H-0003: supersedes H-0002" in md


def test_failed_checks_put_everything_back(toy, site, tmp_path, monkeypatch, capsys):
    cfg = toy.cfg.path
    cfg.write_text(cfg.read_text() + "apply:\n  verify: [\"false\"]\n")
    wb = Workbench.open(cfg)
    before = {p: p.read_text() for p in (wb.cfg.decisions, wb.cfg.schema_dir / "things.yaml")}
    site["body"]["decisions"] = [hosted(wb, 1, "term:TOY_0002", "edit_description", {"description": "Sky."})]
    code, _ = run_pull(wb, site, tmp_path, monkeypatch)
    assert code == 1 and "applying failed" in capsys.readouterr().err
    assert {p: p.read_text() for p in before} == before


def test_bad_key_and_missing_key(toy, site, tmp_path, monkeypatch, capsys):
    code, _ = run_pull(toy, site, tmp_path, monkeypatch, key="nope")
    assert code == 1 and "HTTP 401: the API key was not accepted" in capsys.readouterr().err
    monkeypatch.delenv(pull.KEY_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        cli.main(["-c", str(toy.cfg.path), "pull", "--url", site["url"]])
    assert "CURATE_API_KEY" in str(e.value)
