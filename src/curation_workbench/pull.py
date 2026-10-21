"""`cwb pull`: bring approved decisions from the hosted site into this checkout.

Decisions are made on the hosted site (curate.databio.org). This command is
how they reach a project's repo, on a laptop or in the project's GitHub
Action (which runs exactly this, then opens a pull request):

1. Fetch the project's approved decisions
   (`GET <url>/api/v1/projects/<slug>/decisions/export`) with a read-only API
   key from `$CURATE_API_KEY`. The site leaves out decisions it is holding
   back (stale, or failing the project's checks) and lists them in `held`.
2. Skip any decision this checkout's decisions.yaml already holds
   (`origin.ref`), so running it twice is harmless.
3. Append the rest to decisions.yaml, renumbered to DEC- ids
   (`store.landing.append_hosted`).
4. Dry-run the apply engine on exactly those ids; leave out any decision it
   refuses (reported, so one bad decision does not block the others).
5. Apply for real: edit the files, regen, run the project's checks
   (workbench.yaml `apply:`). Non-terminal decisions (`defer`) are recorded but
   change no files.

Nothing is committed. On any failure every file, decisions.yaml included, is
put back as it was. Uses only the standard library, so a base install works.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import apply
from .store import StoreError, landing

DEFAULT_URL = "https://curate.databio.org"
URL_ENV = "CWB_URL"
KEY_ENV = "CURATE_API_KEY"
TIMEOUT_S = 60


class PullError(Exception):
    pass


@dataclass
class Pulled:
    applied: list = field(default_factory=list)     # [(hosted record, DEC id)] terminal, applied
    recorded: list = field(default_factory=list)    # [(hosted record, DEC id)] non-terminal, recorded only
    refused: dict = field(default_factory=dict)     # {H id: [reason]} the apply engine said no
    held: list = field(default_factory=list)        # [{id, reason}] the site held back
    already: list = field(default_factory=list)     # [H id] already in decisions.yaml
    log: list = field(default_factory=list)

    @property
    def changed(self):
        return bool(self.applied or self.recorded)


def fetch(url, slug, key, *, timeout=TIMEOUT_S) -> dict:
    """The export body: {"decisions": [...], "held": [...], "commit": sha|None}."""
    target = f"{url.rstrip('/')}/api/v1/projects/{urllib.parse.quote(slug)}/decisions/export"
    req = urllib.request.Request(target, headers={"Authorization": f"Bearer {key}",
                                                  "Accept": "application/json",
                                                  "User-Agent": "cwb-pull"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail")
        except Exception:  # noqa: BLE001 - any unreadable body
            detail = None
        hint = {401: "the API key was not accepted (wrong, revoked or expired)",
                403: "the API key may not read this project",
                404: f"no project {slug!r} that this key's owner is on",
                503: "the site has not synced this project yet"}.get(e.code, "")
        raise PullError(f"{target}: HTTP {e.code}"
                        + (f": {hint}" if hint else "")
                        + (f" ({detail})" if isinstance(detail, str) else "")) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise PullError(f"{target}: {e}") from None
    if not isinstance(body, dict) or not isinstance(body.get("decisions"), list):
        raise PullError(f"{target}: unexpected response (no decisions list)")
    return body


def _drop_orphans(todo, landed, refused):
    """Leave out records that supersede an H- decision that is neither landed
    nor still in `todo` (it was refused or held back); repeat until stable."""
    while True:
        ids = {r["id"] for r in todo}
        bad = [r for r in todo if isinstance(r.get("supersedes"), str)
               and r["supersedes"].startswith("H-") and r["supersedes"] not in ids
               and r["supersedes"] not in landed]
        if not bad:
            return todo
        for r in bad:
            refused[r["id"]] = [f"supersedes {r['supersedes']}, which is not coming in this pull"]
        todo = [r for r in todo if r["id"] not in refused]


def run(wb, export) -> Pulled:
    """Append and apply the export's decisions in `wb`'s checkout (see the
    module docstring). Raises PullError (with every file restored) on failure."""
    out = Pulled(held=list(export.get("held") or []))
    path = wb.cfg.decisions
    original = path.read_text()
    landed = landing.landed_refs(wb.store.records())
    out.already = [d["id"] for d in export["decisions"] if d["id"] in landed]
    todo = _drop_orphans([d for d in export["decisions"] if d["id"] not in landed],
                         landed, out.refused)
    if not todo:
        return out
    try:
        while True:
            path.write_text(original)
            renumber = landing.append_hosted(path, path, todo)
            back = {dec: h for h, dec in renumber.items()}
            dry = apply.run(wb, want=list(back), dry_run=True)
            if dry.ok:
                break
            own = {back[k]: v for k, v in dry.errors.items() if k in back}
            if not own:
                raise PullError("the apply engine refused the whole run: " + "; ".join(
                    f"{k}: {e}" for k, errs in dry.errors.items() for e in errs))
            out.refused.update(own)
            todo = _drop_orphans([r for r in todo if r["id"] not in own], landed, out.refused)
            if not todo:
                path.write_text(original)
                return out
        applied_ids = {r["id"] for r in dry.records}
        real = apply.run(wb, want=list(back), regen_if_empty=True)
        out.log = real.log
        if not real.ok:
            raise PullError("applying failed; every file was put back:\n" + "\n".join(
                f"  {k}: {e}" for k, errs in real.errors.items() for e in errs))
    except BaseException:
        path.write_text(original)
        raise
    for r in todo:
        (out.applied if renumber[r["id"]] in applied_ids else out.recorded).append((r, renumber[r["id"]]))
    return out


def _who(decided_by):
    return str(decided_by or "").split(" <", 1)[0]


def summary(pulled, *, url, slug, commit=None) -> str:
    """Markdown for people (the pull request body)."""
    site = f"{url.rstrip('/')}/p/{slug}"
    lines = [f"Decisions approved by curators on [{url.rstrip('/').split('://')[-1]}]({site}), "
             "applied with `cwb pull`. The project's checks passed after applying them.", ""]

    def table(rows):
        lines.extend(["| Site id | Repo id | Subject | Decision | Decided by |", "|---|---|---|---|---|"])
        for r, dec in rows:
            lines.append(f"| {r['id']} | {dec} | `{r['subject']}` | {r['verdict']} | {_who(r.get('decided_by'))} |")
        lines.append("")
    if pulled.applied:
        lines.append(f"**Applied ({len(pulled.applied)}):**")
        lines.append("")
        table(pulled.applied)
    if pulled.recorded:
        lines.append(f"**Recorded only ({len(pulled.recorded)}):** not final (for example *defer*), "
                     "so they are added to `curation/decisions.yaml` without changing other files.")
        lines.append("")
        table(pulled.recorded)
    if pulled.refused:
        lines.append(f"**Left out ({len(pulled.refused)}):** the apply engine refused these; a curator "
                     "should look at them on the site.")
        lines.extend(f"- {h}: {'; '.join(map(str, errs))}" for h, errs in sorted(pulled.refused.items()))
        lines.append("")
    if pulled.held:
        lines.append(f"**Held back on the site ({len(pulled.held)}):** not sent, because the schema changed "
                     "since they were decided or they fail the project's checks.")
        lines.extend(f"- {h.get('id')}: {h.get('reason')}" for h in pulled.held)
        lines.append("")
    if not pulled.changed:
        lines.append("Nothing new to apply.")
        lines.append("")
    lines.append("After this is merged, the site marks these decisions as applied on its next sync"
                 + (f" (it last synced at commit `{commit[:12]}`)." if commit else "."))
    return "\n".join(lines) + "\n"


def main(wb, *, project=None, url=None, summary_path=None) -> int:
    slug = project or wb.cfg.data.get("project")
    url = url or os.environ.get(URL_ENV) or DEFAULT_URL
    key = os.environ.get(KEY_ENV, "").strip()
    if not slug:
        sys.exit("cwb pull: no project given and workbench.yaml has no `project:`")
    if not key:
        sys.exit(f"cwb pull: set {KEY_ENV} to a read-only API key from {url} "
                 "(a curator makes one on the project's Approved page)")
    try:
        export = fetch(url, slug, key)
        print(f"{url}: {len(export['decisions'])} approved decision(s) for {slug}"
              + (f", {len(export.get('held') or [])} held back" if export.get("held") else ""))
        pulled = run(wb, export)
    except (PullError, StoreError, apply.ApplyError) as e:
        print(f"cwb pull FAILED: {e}", file=sys.stderr)
        return 1
    for line in pulled.log:
        print(line)
    for h in pulled.already:
        print(f"  {h}: already in decisions.yaml; skipped")
    for r, dec in pulled.applied:
        print(f"  {r['id']} -> {dec} applied ({r['verdict']} {r['subject']})")
    for r, dec in pulled.recorded:
        print(f"  {r['id']} -> {dec} recorded ({r['verdict']} {r['subject']}; changes no files)")
    for h, errs in sorted(pulled.refused.items()):
        print(f"  {h}: left out: {'; '.join(map(str, errs))}")
    for h in pulled.held:
        print(f"  {h.get('id')}: held back on the site: {h.get('reason')}")
    if pulled.changed:
        print(f"pulled {len(pulled.applied) + len(pulled.recorded)} decision(s); nothing is committed: "
              "look over the changes (git diff) and commit them")
    else:
        print("nothing new to apply")
    if summary_path:
        with open(summary_path, "w") as fh:
            fh.write(summary(pulled, url=url, slug=slug, commit=export.get("commit")))
    return 0


__all__ = ["DEFAULT_URL", "KEY_ENV", "PullError", "Pulled", "URL_ENV", "fetch", "main", "run", "summary"]
