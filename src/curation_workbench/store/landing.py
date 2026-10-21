"""Hosted decisions landing in a repo's decisions.yaml.

Decisions are made on the hosted site (`H-NNNN`, Postgres). They reach a
project's repo when `cwb pull` (run by hand or by the repo's GitHub Action)
appends them to `curation/decisions.yaml`, renumbered into the file's `DEC-`
sequence, and the apply engine applies them. Each keeps
`origin: {kind: hosted, ref: <its H- id>}`; that ref is how the sync worker
later recognizes it and marks the hosted copy applied.

Plain Python only (no SQLAlchemy), so `cwb pull` runs on a base install.

  append_hosted(template, out, records) -> {H- id: DEC- id}
  landed_refs(repo_records) -> {H- id: repo record}
"""
from __future__ import annotations

from .. import yamlio
from .validate import StoreError, to_plain
from .yaml_store import YamlStore, _as_date, _decisions_seq, _to_yaml

#: Keys a hosted record may carry that never go into decisions.yaml.
INTERNAL_KEYS = ("rev", "vote_ids", "stale", "stale_reason", "stale_new_sid", "decided_by_user_id",
                 "plugin_errors")


def append_hosted(template_path, out_path, records) -> dict[str, str]:
    """Append hosted `records` (plain dicts, oldest first) to the decisions
    file at `template_path` and write the result to `out_path`.

    The template's header, style and records are kept. Appended records are
    renumbered into the file's DEC- sequence from its `next_id`; each keeps
    `origin: {kind: hosted, ref: <its H- id>}`, a `supersedes` that names an
    H- id (appended now, or already in the file) is rewritten to its DEC- id,
    and internal keys (`rev`, vote links, stale marks, plugin errors) are
    dropped. With nothing to append the output
    is byte-identical to the template. Returns {H- id: new DEC- id}.
    """
    doc = YamlStore(template_path).load()
    landed = {h: r["id"] for h, r in landed_refs(to_plain(dict(doc.data)).get("decisions") or []).items()}
    n = int(doc.data["next_id"])
    renumber = {}
    for r in records:
        renumber[r["id"]] = f"DEC-{n:04d}"
        n += 1
    known = {**landed, **renumber}
    errors = [f"{r['id']} supersedes {r['supersedes']}, which is neither in this export nor in the file"
              for r in records
              if isinstance(r.get("supersedes"), str) and r["supersedes"].startswith("H-")
              and r["supersedes"] not in known]
    if errors:
        raise StoreError(400, errors)
    if records:
        seq = _decisions_seq(doc)
        for r in records:
            rec = {k: v for k, v in r.items() if k not in INTERNAL_KEYS}
            rec["id"] = renumber[r["id"]]
            rec["origin"] = {"kind": "hosted", "ref": r["id"]}
            if rec.get("supersedes") in known:
                rec["supersedes"] = known[rec["supersedes"]]
            rec["decided_on"] = _as_date(rec["decided_on"])
            if isinstance(rec.get("applied"), dict):
                rec["applied"] = {**rec["applied"], "on": _as_date(rec["applied"].get("on"))}
            seq.append(_to_yaml(rec))
        doc.data["next_id"] = n
    yamlio.dump(doc, out_path)
    return renumber


def landed_refs(repo_records) -> dict[str, dict]:
    """{H- id: repo record} for every repo record that came from the hosted site."""
    return {o["ref"]: r for r in repo_records
            if (o := r.get("origin") or {}).get("kind") == "hosted" and o.get("ref")}


__all__ = ["INTERNAL_KEYS", "append_hosted", "landed_refs"]
