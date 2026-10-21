"""Helpers for the hosted API tests (imported as `from .hostedtools import ...`)."""
from __future__ import annotations

from pathlib import Path

from curation_workbench.hosted import project_data
from curation_workbench.hosted.cli import dev_snapshot

ADMIN_EMAIL = "admin@example.org"          # superadmin
SITE_ADMIN_EMAIL = "siteadmin@example.org"  # admin (snapgate user admin only)
TOY = Path(__file__).resolve().parents[1] / "fixtures" / "toy"


def publish(engine, slug, root):
    """The `cwb hosted admin dev-snapshot` logic: publish `root`'s snapshot."""
    project_data.clear_cache()
    return dev_snapshot(engine, slug, root / "workbench.yaml")


def vote_body(toy_hash, sid="term:TOY_0001", review_type="vocabulary/links", answer="keep",
              verdict="keep", **kw):
    body = {"sid": sid, "review_type": review_type, "answer": answer,
            "subject_hash": toy_hash(sid),
            "proposal": {"verdict": verdict, "rationale": "looks right", "evidence": []}}
    body.update(kw)
    return body


def decision_body(toy_hash, sid="term:TOY_0001", verdict="keep", **kw):
    body = {"subject": sid, "verdict": verdict, "rationale": "fine",
            "subject_hashes": {sid: toy_hash(sid)}, "evidence": [{"kind": "other", "detail": "x"}]}
    body.update(kw)
    return body


def add_repo_decision(toy_root):
    """A DEC- decision in the repo's decisions.yaml (it reaches hosted mode through the snapshot)."""
    from curation_workbench import snapshot, store, yamlio
    from curation_workbench.store.yaml_store import YamlStore, _decisions_seq, _to_yaml
    from curation_workbench.workbench import Workbench
    wb = Workbench.open(toy_root / "workbench.yaml")
    b = snapshot.build(wb)
    subj = store.Subjects(b["subjects"])
    sid = "term:TOY_0002"
    ys = YamlStore(wb.cfg.decisions)
    doc = ys.load()
    n = int(doc.data["next_id"])
    rec = store.normalize({
        "id": f"DEC-{n:04d}", "subject": sid, "verdict": "keep", "rationale": "repo says so",
        "decided_by": "tester", "decided_on": "2026-10-01", "subject_hashes": {sid: subj.get(sid)["hash"]},
        "schema_fingerprint": subj.fingerprint, "origin": {"kind": "local", "ref": None}})
    _decisions_seq(doc).append(_to_yaml(rec))
    doc.data["next_id"] = n + 1
    yamlio.dump(doc, wb.cfg.decisions)
    return store.to_plain(rec)
