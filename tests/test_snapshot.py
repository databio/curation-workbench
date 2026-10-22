"""snapshot + derive: the toy bundle round-trips through JSON and derives the
same status.json as `cwb status`; signals never see decisions."""
import json
import shutil
from pathlib import Path

import pytest

from curation_workbench import cli, derive, snapshot, status
from curation_workbench.workbench import Workbench

TOY = Path(__file__).parent / "fixtures" / "toy"


@pytest.fixture
def toy(tmp_path):
    root = tmp_path / "toy"
    shutil.copytree(TOY, root)
    return root


def test_bundle_round_trip(toy):
    wb = Workbench.open(toy / "workbench.yaml")
    bundle = snapshot.build(wb)
    assert bundle["format"] == "cwb-snapshot/1"
    assert bundle["schema_fingerprint"] == bundle["subjects"]["schema_fingerprint"]
    assert bundle["review_types"]["title"] == "Toy" and bundle["plugins"] == []
    assert all(bundle[k] == {} for k in snapshot.REVIEW_HOOK_KEYS)  # no plugins, no hook output
    assert set(bundle["signals"]) == {"impact", "adoption", "required"}
    again = json.loads(snapshot.dumps(bundle))
    text, _ = status.render(wb)
    assert status.dump(derive.status(again, wb.store.records())) == text


def test_cli_snapshot_and_derive(toy, tmp_path, capsys):
    cfg = str(toy / "workbench.yaml")
    assert cli.main(["-c", cfg, "regen"]) == 0
    assert cli.main(["-c", cfg, "regen", "--check"]) == 0
    out = tmp_path / "bundle.json"
    assert cli.main(["-c", cfg, "snapshot", "--out", str(out)]) == 0
    dest = tmp_path / "derived"
    assert cli.main(["derive", "--snapshot", str(out), "--decisions",
                     str(toy / "curation" / "decisions.yaml"), "--out", str(dest)]) == 0
    assert (dest / "status.json").read_text() == (toy / "curation" / "status.json").read_text()
    js = tmp_path / "decs.json"
    js.write_text("[]")
    assert cli.main(["derive", "--snapshot", str(out), "--decisions", str(js),
                     "--out", str(dest)]) == 0
    assert (dest / "status.json").read_text() == (toy / "curation" / "status.json").read_text()


def test_regen_writes_the_committed_snapshot_file(toy, capsys):
    """With `snapshot:` in workbench.yaml, regen writes the bundle there (what
    the website downloads), `--check` guards it, and `cwb snapshot` matches."""
    cfg = toy / "workbench.yaml"
    cfg.write_text(cfg.read_text() + "snapshot: curation/snapshot.json\n")
    path = toy / "curation" / "snapshot.json"
    assert cli.main(["-c", str(cfg), "regen"]) == 0
    bundle = snapshot.check_format(json.loads(path.read_text()))
    assert bundle == snapshot.build(Workbench.open(cfg))
    assert cli.main(["-c", str(cfg), "snapshot", "--check"]) == 0
    assert cli.main(["-c", str(cfg), "regen", "--check"]) == 0
    path.write_text("{}\n")
    assert cli.main(["-c", str(cfg), "snapshot", "--check"]) == 1
    assert cli.main(["-c", str(cfg), "regen", "--check"]) == 1
    assert cli.main(["-c", str(cfg), "snapshot"]) == 0
    assert cli.main(["-c", str(cfg), "regen", "--check"]) == 0


def test_signal_cannot_read_decisions(toy):
    (toy / "peek.py").write_text(
        "def register(wb):\n    wb.signals['peek'] = lambda ctx: dict(ctx.last_terminal)\n")
    cfg = toy / "workbench.yaml"
    cfg.write_text(cfg.read_text() + "plugins: [peek]\n")
    pol = toy / "curation" / "policy.yaml"
    pol.write_text(pol.read_text().replace("queue:\n", "queue:\n  peek: 1.0\n"))
    wb = Workbench.open(cfg)
    with pytest.raises(AttributeError):
        snapshot.build(wb)


def test_masked_signal_zeroed_by_terminal_decision(toy):
    wb = Workbench.open(toy / "workbench.yaml")
    bundle = snapshot.build(wb)
    bundle["masked_signals"] = {"required": {"slot:color": 1, "slot:sample_id": 1}}
    bundle["signals"].pop("required")
    st = derive.status(bundle, [])
    assert st["subjects"]["slot:color"]["signals"]["required"] == 1
    sid = "slot:color"
    keep = {"id": "DEC-0001", "subject": sid, "verdict": "keep", "evidence": [],
            "subject_hashes": {sid: bundle["subjects"]["subjects"][sid]["hash"]}, "status": "pending"}
    st = derive.status(bundle, [keep])
    assert st["subjects"][sid]["signals"]["required"] == 0
    assert st["subjects"]["slot:sample_id"]["signals"]["required"] == 1


def test_merge_decisions():
    repo = [{"id": "DEC-0001", "origin": {"kind": "local", "ref": None}},
            {"id": "DEC-0002", "origin": {"kind": "hosted", "ref": "H-0001"}}]
    hosted = [{"id": "H-0001"}, {"id": "H-0002"}]
    assert [d["id"] for d in derive.merge_decisions(repo, hosted)] == ["DEC-0001", "DEC-0002", "H-0002"]


def test_review_hooks_fill_the_bundle(toy):
    (toy / "uihooks.py").write_text(
        "def register(wb):\n"
        "    wb.suggestions.append(lambda wb: {'map:1': {'object': 'ex:X', 'label': 'x', 'source': 'ex',"
        " 'score': 0.9, 'definition': None}})\n"
        "    wb.browse_url = lambda wb, s: f'https://site/{s[\"slug\"]}' if s['kind'] == 'term' else None\n"
        "    wb.subject_panels.append(lambda wb: {'term:TOY_0001': [{'title': 'P', 'text': 't'}]})\n"
        "    wb.review_prompts.append(lambda wb: {sid: [{'title': 'Q', 'text': str(wb.snapshot_view['signals']"
        "['adoption'].get(sid, 0))}] for sid in wb.snapshot_view['subjects'] if sid == 'term:TOY_0001'})\n")
    cfg = toy / "workbench.yaml"
    cfg.write_text(cfg.read_text() + "plugins: [uihooks]\n")
    wb = Workbench.open(cfg)
    bundle = snapshot.build(wb)
    assert bundle["suggestions"]["map:1"]["object"] == "ex:X"
    assert set(bundle["browse"]) == {"term:TOY_0001", "term:TOY_0002"}
    assert bundle["panels"] == {"term:TOY_0001": [{"title": "P", "text": "t"}]}
    assert bundle["prompts"]["term:TOY_0001"][0]["text"] == "1"
    assert wb.snapshot_view is None
