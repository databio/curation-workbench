"""Curation status: a state and a queue score for every subject.

The work is split in two (see snapshot.py and derive.py):

* `snapshot.build(wb)` computes everything that depends only on the repo at one
  commit: the subject registry, aliases, lint and plugin findings, and every
  queue signal. Plugin code runs only there.
* `derive.status(bundle, decisions)` turns that bundle plus a decision list into
  `status.json`. It is generic code with no plugin calls.

`build(wb)` / `render(wb)` here are the local-mode pair of both, over the
workbench's own decision store. status.json:

  {_generated_by, schema_fingerprint, weights,
   sources:  [{path, findings, stale}],
   subjects: {sid: {kind, container, layer, retired, state, open_findings,
                    decisions, latest, signals, score}},
   rollups:  {overall, by_kind, by_layer, by_container},
   queue:    [sid ...]}                          # open | stale | unreviewed, score desc

States: unreviewed (no decision, no open finding), open (an open finding, with
or without earlier decisions), deferred (newest decision is `defer`), settled
(terminal, not stale), applied (settled and `status: applied`), stale (the
newest terminal decision's recorded hash differs from the current one).

A finding is open for a subject unless a live terminal decision cites it in its
evidence, or a current terminal decision on the subject cites no finding at all
(a blanket judgment). A finding with an `object` is also closed once the term
has an SSSOM row for that object. Findings files whose `schema_fingerprint`
differs from subjects.json count as stale and contribute nothing.

Signals: one per `queue:` weight, in the weights' order. `findings` and
`stale` come from the state computation; every other name is looked up in
`wb.signals` (builtin: impact, adoption, required, machine) or
`wb.masked_signals` (builtin: port; zeroed where a terminal decision exists)
and is 0 when nothing provides it. score = sum(weight * signal). Signals and
findings never see decisions: a signal function gets a `Context` with no
decision data.
"""
import json
from pathlib import Path

import yaml


STATES = ("unreviewed", "open", "deferred", "settled", "applied", "stale")
QUEUE_STATES = ("open", "stale", "unreviewed")
GENERATED_BY = "cwb status; do not edit"


def load_yaml(path, default=None):
    if not Path(path).exists():
        return default
    return yaml.safe_load(Path(path).read_text()) or default


def resolve(sid, aliases):
    seen = set()
    while sid in aliases and sid not in seen:
        seen.add(sid)
        sid = aliases[sid]
    return sid


def load_findings(wb, fingerprint, severity_of, generated=None):
    """([finding], [source summary]) from the findings dir and every plugin source.

    `generated` maps a findings file name (e.g. "schema_lint.json") to its
    document computed in memory; it is used instead of the file on disk."""
    generated = generated or {}
    names = sorted({p.name for p in wb.cfg.findings_dir.glob("*.json")} | set(generated))
    findings, sources = [], []
    for name in names:
        path = wb.cfg.findings_dir / name
        data = generated[name] if name in generated else json.loads(path.read_text())
        stale = data.get("schema_fingerprint") != fingerprint
        items = [] if stale else data.get("findings") or []
        sources.append({"path": wb.cfg.rel(path), "findings": len(items), "stale": stale})
        findings += [{**f, "source": path.stem} for f in items]
    for fn in wb.findings_sources:
        f, s = fn(wb, fingerprint, severity_of)
        findings += f
        sources += s
    return findings, sources


class Context:
    """What a signal function sees: the workbench, the subject registry and the
    term labels. It carries no decisions (signals are computed once per commit;
    see derive.py for the one decision-dependent mask)."""

    def __init__(self, wb, subjects, labels):
        self.wb = wb
        self.subjects = subjects
        self.labels = labels


def build(wb):
    """status.json data for the workbench's project and its own decision store."""
    from . import derive, snapshot
    return derive.status(snapshot.build(wb), wb.store.records())


def dump(data):
    return json.dumps(data, indent=1, ensure_ascii=False, default=str) + "\n"


def summary(data):
    o = data["rollups"]["overall"]
    return (f"status: {o['total']} subjects, " + " ".join(f"{s}={o[s]}" for s in STATES)
            + f", {o['percent_settled']}% settled, queue {len(data['queue'])}")


def render(wb):
    data = build(wb)
    return dump(data), summary(data)
