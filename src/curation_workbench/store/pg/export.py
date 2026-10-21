"""Export hosted decisions into a repo's decisions.yaml (the seam for the PR path).

There is no import: the repo's own decisions reach the hosted service through
the project snapshot (`bundle.repo_decisions`), so Postgres never holds a copy.
"""
from __future__ import annotations

from ... import yamlio
from ..validate import StoreError
from ..yaml_store import YamlStore, _as_date, _decisions_seq, _to_yaml
from .store import INTERNAL_KEYS


def export_yaml(store, template_path, out_path, *, ids=None) -> dict[str, str]:
    """Append `store`'s pending hosted decisions (or only `ids`) to the decisions
    file at `template_path` and write the result to `out_path`.

    The template's header, style and records are kept. Appended records are
    renumbered into the file's DEC- sequence from its `next_id`, oldest first;
    each keeps `origin: {kind: hosted, ref: <its H- id>}`, a `supersedes` that
    names another exported H- id is rewritten, and `rev`, `vote_ids` and the
    stale keys are dropped. With nothing to export the output is byte-identical
    to the template. Returns {H- id: new DEC- id}.
    """
    doc = YamlStore(template_path).load()
    recs = store.records()
    if ids is None:
        chosen = [r for r in recs if r["status"] == "pending"]
    else:
        want = set(ids)
        known = {r["id"] for r in recs}
        missing = sorted(want - known)
        if missing:
            raise StoreError(404, [f"no decision {d}" for d in missing])
        chosen = [r for r in recs if r["id"] in want]
        not_pending = [f"{r['id']} is {r['status']}; only pending decisions are exported"
                       for r in chosen if r["status"] != "pending"]
        if not_pending:
            raise StoreError(400, not_pending)

    n = int(doc.data["next_id"])
    renumber = {}
    for r in chosen:
        renumber[r["id"]] = f"DEC-{n:04d}"
        n += 1
    errors = [f"{r['id']} supersedes {r['supersedes']}, which is not in this export"
              for r in chosen
              if isinstance(r.get("supersedes"), str) and r["supersedes"].startswith("H-")
              and r["supersedes"] not in renumber]
    if errors:
        raise StoreError(400, errors)

    if chosen:
        seq = _decisions_seq(doc)
        for r in chosen:
            rec = {k: v for k, v in r.items() if k not in INTERNAL_KEYS}
            rec["id"] = renumber[r["id"]]
            rec["origin"] = {"kind": "hosted", "ref": r["id"]}
            if rec.get("supersedes") in renumber:
                rec["supersedes"] = renumber[rec["supersedes"]]
            rec["decided_on"] = _as_date(rec["decided_on"])
            if isinstance(rec.get("applied"), dict):
                rec["applied"] = {**rec["applied"], "on": _as_date(rec["applied"].get("on"))}
            seq.append(_to_yaml(rec))
        doc.data["next_id"] = n
    yamlio.dump(doc, out_path)
    return renumber


__all__ = ["export_yaml"]
