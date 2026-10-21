"""Export hosted decisions into a repo's decisions.yaml (`cwb db export`).

The renumbering itself is `store.landing.append_hosted`, which `cwb pull`
uses too. There is no import: the repo's own decisions reach the hosted
service through the project snapshot (`bundle.repo_decisions`), so Postgres
never holds a copy.
"""
from __future__ import annotations

from ..landing import append_hosted
from ..validate import StoreError


def export_yaml(store, template_path, out_path, *, ids=None) -> dict[str, str]:
    """Append `store`'s pending hosted decisions (or only `ids`) to the decisions
    file at `template_path` and write the result to `out_path` (see
    `append_hosted`). Returns {H- id: new DEC- id}."""
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
    return append_hosted(template_path, out_path, chosen)


__all__ = ["export_yaml"]
