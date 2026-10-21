"""The isolated runner: wire format safety, one job at a time, timeouts,
structured errors, and the reserved apply mode. Runs on 127.0.0.1 with an
ephemeral port; no network beyond loopback."""
import gzip
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from synctools import make_repo

from curation_workbench import runner
from curation_workbench.hosted.sync import gitrepo
from curation_workbench.hosted.sync.remote import LocalRunner, RemoteRunner, RunnerError
from curation_workbench.runner import Member


def tree_of(path):
    path = Path(path)
    out = []
    for p in sorted(path.rglob("*")):
        rel = p.relative_to(path).as_posix()
        if rel.startswith(".git"):
            continue
        out.append(Member(rel, "dir", None) if p.is_dir() else Member(rel, "file", p.read_bytes()))
    return out


@pytest.fixture
def toy_tree(tmp_path):
    return tree_of(make_repo(tmp_path / "repo"))


@pytest.fixture
def server():
    started = []

    def start(run_fn=runner.in_process_runner, timeout_s=None):
        srv = runner.RunnerServer(("127.0.0.1", 0), runner=run_fn, timeout_s=timeout_s)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        started.append(srv)
        return srv, f"http://127.0.0.1:{srv.server_address[1]}"
    yield start
    for s in started:
        s.shutdown()
        s.server_close()


def post(url, body):
    req = urllib.request.Request(f"{url}/run", data=body, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers.get("Content-Type"), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()


def job(mode="snapshot", plugins=True, **kw):
    return {"mode": mode, "config_path": "workbench.yaml", "plugins_enabled": plugins, **kw}


def test_snapshot_and_checks(server, toy_tree):
    _, url = server()
    status, ctype, body = post(url, runner.pack_job(job(), toy_tree))
    assert status == 200 and ctype == "application/gzip"
    bundle = json.loads(gzip.decompress(body))
    assert bundle["plugins"] == ["toy_plugin"] and bundle["decision_hooks"] is True
    assert any(f["id"] == "plug:color" for f in bundle["findings"])
    h = bundle["subjects"]["subjects"]["term:TOY_0002"]["hash"]
    decs = [{"id": "H-0001", "subject": "term:TOY_0002", "verdict": "keep", "rationale": "x",
             "decided_by": "a", "decided_on": "2026-10-21", "subject_hashes": {"term:TOY_0002": h},
             "schema_fingerprint": "f", "origin": {"kind": "hosted", "ref": "H-0001"}, "rev": 3,
             "vote_ids": [], "stale": False}]
    rr = RemoteRunner(url)
    assert rr.checks(toy_tree, "workbench.yaml", True, decs) == {
        "H-0001": ["the toy plugin refuses decisions about blue"]}
    assert rr.snapshot(toy_tree, "workbench.yaml", False)["plugins"] == []


def test_generic_only_and_committed_outputs_warning(server, tmp_path):
    repo = make_repo(tmp_path / "r")
    (repo / "curation" / "subjects.json").write_text('{"schema_fingerprint": "old"}\n')
    _, url = server()
    bundle = RemoteRunner(url).snapshot(tree_of(repo), "workbench.yaml", False)
    assert not any(f["id"] == "plug:color" for f in bundle["findings"])
    assert any(w.startswith("committed outputs are out of date: curation/subjects.json")
               for w in bundle["warnings"])


def test_structured_errors(server, tmp_path, toy_tree):
    _, url = server()
    rr = RemoteRunner(url)
    broken = [m if m.name != "plugin/toy_plugin.py" else Member(m.name, "file", b"import nope_x\n")
              for m in toy_tree]
    with pytest.raises(RunnerError) as e:
        rr.snapshot(broken, "workbench.yaml", True)
    assert e.value.code == "plugin_import_failed"
    escaped = [m if m.name != "workbench.yaml" else
               Member(m.name, "file", m.data + b"review_types: ../../x.yaml\n") for m in toy_tree]
    with pytest.raises(RunnerError) as e:
        rr.snapshot(escaped, "workbench.yaml", False)
    assert e.value.code == "path_escape"
    with pytest.raises(RunnerError) as e:
        rr.snapshot(toy_tree, "nope.yaml", False)
    assert e.value.code == "config_invalid"
    bad_schema = [m if m.name != "src/things.yaml" else Member(m.name, "file", m.data.replace(
        b"meaning: toy:TOY_0002", b"meaning: toy:NOPE")) for m in toy_tree]
    with pytest.raises(RunnerError) as e:
        rr.snapshot(bad_schema, "workbench.yaml", False)
    assert e.value.code == "registry_error" and e.value.detail


def test_apply_mode_reserved_and_bad_requests(server, toy_tree):
    _, url = server()
    status, _, body = post(url, runner.pack_job(job("apply"), toy_tree))
    assert status == 400 and json.loads(body)["error"]["code"] == "bad_request"
    status, _, body = post(url, b"not a tar")
    assert status == 400
    status, _, body = post(url, runner.pack_job({**job(), "extra": 1}, toy_tree))
    assert status == 400


def test_tar_safety():
    def raw(name, kind="f", data=b"x"):
        import io
        import tarfile
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:") as tar:
            info = tarfile.TarInfo(name)
            if kind == "sym":
                info.type, info.linkname = tarfile.SYMTYPE, data
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()
    for bad in (raw("../x"), raw("/etc/x"), raw("tree/a", "sym", "../../x")):
        with pytest.raises(runner.RunError):
            runner.read_members(bad, max_bytes=100)
    with pytest.raises(runner.RunError) as e:
        runner.read_members(raw("big", data=b"x" * 50), max_bytes=10)
    assert e.value.code == "too_large"
    assert runner.read_members(raw("tree/a", "sym", "b"), max_bytes=10)[0].kind == "symlink"


def test_busy_returns_409(server, toy_tree):
    gate, entered = threading.Event(), threading.Event()

    def slow(jobdir, timeout_s):
        entered.set()
        gate.wait(10)
        return runner.run_job_dir(jobdir)
    srv, url = server(run_fn=slow)
    body = runner.pack_job(job(), toy_tree)
    t = threading.Thread(target=post, args=(url, body))
    t.start()
    assert entered.wait(10)
    status, _, data = post(url, body)
    assert status == 409 and json.loads(data)["error"]["code"] == "busy"
    with pytest.raises(RunnerError) as e:
        RemoteRunner(url).snapshot(toy_tree, "workbench.yaml", True)
    assert e.value.code == "runner_busy"
    gate.set()
    t.join()
    assert RemoteRunner(url).get_json("/health")["busy"] is False


def test_timeout_in_child_process(toy_tree):
    with pytest.raises(RunnerError) as e:
        LocalRunner(timeout_s=0.01).snapshot(toy_tree, "workbench.yaml", True)
    assert e.value.code == "timeout"


def test_child_process_runner(toy_tree):
    bundle = LocalRunner(timeout_s=120).snapshot(toy_tree, "workbench.yaml", True)
    assert bundle["plugins"] == ["toy_plugin"]


def test_unreachable():
    with pytest.raises(RunnerError) as e:
        RemoteRunner("http://127.0.0.1:9", timeout_s=1).snapshot([], "workbench.yaml", False)
    assert e.value.code == "runner_unreachable"


def test_archive_to_runner(tmp_path, server):
    origin = make_repo(tmp_path / "origin")
    repo = gitrepo.GitRepo(tmp_path / "m.git", f"file://{origin}", "master", sync_dir=tmp_path,
                           allow_file=True)
    sha = repo.fetch()
    tree = repo.plugin_tree(sha, "plugin")
    members = repo.archive(sha, "plugin", repo.tree_tar_gz(tree))
    _, url = server()
    assert RemoteRunner(url).snapshot(members, "workbench.yaml", True)["plugins"] == ["toy_plugin"]
