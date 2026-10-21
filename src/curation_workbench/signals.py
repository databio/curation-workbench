"""Generic queue signals. Each takes a status.Context and returns {sid: value};
a subject missing from the result scores 0 for that signal.

Configured through `options:` in workbench.yaml:
  adoption_root         SID of the class whose reachable subjects get `adoption`
  import_source_prefix  classes whose `source:` starts with this are imported
                        ("ported"); they and the slots/enums in their modules get
                        `port` until a terminal decision covers them (`port` is a
                        masked signal: derive.py zeroes it where one exists)
  machine_definitions   YAML file {definitions: [{subject, description}]}: subjects
                        whose description still equals the machine draft get `machine`
Term usage counts for `impact` come from `wb.term_usage` hooks ((wb) -> {sid: n}).
"""
import math
from collections import defaultdict

from .schema import slot_ranges
from .status import load_yaml


def effective_slots(cname, subjects):
    """Slot names a class lists or inherits (is_a + mixins, transitively)."""
    out, seen, todo = [], set(), [cname]
    while todo:
        c = todo.pop()
        if c in seen:
            continue
        seen.add(c)
        body = (subjects.get(f"class:{c}") or {}).get("payload") or {}
        out += body.get("slots") or []
        todo += ([body["is_a"]] if body.get("is_a") else []) + list(body.get("mixins") or [])
    return out


def adoption(ctx):
    """1 for SIDs reachable from the adoption root through effective slots and
    their class and enum ranges (plus class ancestors and enum terms)."""
    root = ctx.wb.option("adoption_root")
    subjects = ctx.subjects
    reach, todo = set(), [root] if root else []
    while todo:
        sid = todo.pop()
        if sid in reach or sid not in subjects:
            continue
        reach.add(sid)
        s = subjects[sid]
        if s["kind"] == "class":
            for slot in effective_slots(s["name"], subjects):
                todo.append(f"slot:{slot}")
            body = s["payload"]
            todo += [f"class:{c}" for c in ([body["is_a"]] if body.get("is_a") else [])
                     + list(body.get("mixins") or [])]
        elif s["kind"] == "slot":
            for r in slot_ranges(s["payload"]):
                todo += [f"class:{r}", f"enum:{r}"]
        elif s["kind"] == "enum":
            todo += s.get("members") or []
    return {sid: 1 for sid in reach}


def term_usage(wb):
    out = {}
    for fn in getattr(wb, "term_usage", []):
        out.update(fn(wb))
    return out


def impact(ctx):
    """0..1 per subject, normalized within each kind: term usage (log10), class
    and slot reach, enum and subset/module size (log2)."""
    subjects = ctx.subjects
    usage = term_usage(ctx.wb)
    raw = {}
    slot_classes = {sid: set(s.get("used_by") or []) | set(s.get("inherited_by") or [])
                    for sid, s in subjects.items() if s["kind"] == "slot"}
    for sid, s in subjects.items():
        k = s["kind"]
        if k == "term":
            raw[sid] = usage.get(sid, 0)
        elif k == "class":
            users = set()
            for slot in s.get("referenced_by") or []:
                users |= slot_classes.get(slot, set())
            raw[sid] = len(users) + len(s.get("inherited_by") or [])
        elif k == "slot":
            raw[sid] = len(slot_classes[sid])
        elif k == "enum":
            raw[sid] = len(s.get("referenced_by") or [])
        elif k in ("subset", "module"):
            raw[sid] = len(s.get("members") or [])
        else:
            raw[sid] = 0
    top = defaultdict(int)
    for sid, v in raw.items():
        top[subjects[sid]["kind"]] = max(top[subjects[sid]["kind"]], v)
    out = {}
    for sid, v in raw.items():
        k = subjects[sid]["kind"]
        log = math.log10 if k == "term" else math.log2
        out[sid] = round(log(1 + v) / log(1 + top[k]), 4) if top[k] else 0.0
    return out


def required(ctx):
    return {sid: 1 for sid, s in ctx.subjects.items()
            if s["kind"] == "slot" and bool(s["payload"].get("required"))}


def ported(ctx):
    """1 for imported classes and the slots/enums in their modules. Registered
    as a masked signal: derive zeroes it once a terminal decision covers the subject."""
    prefix = ctx.wb.option("import_source_prefix")
    if not prefix:
        return {}
    subjects = ctx.subjects
    classes = {sid for sid, s in subjects.items() if s["kind"] == "class"
               and str(s["payload"].get("source") or "").startswith(prefix)}
    modules = {subjects[sid]["module"] for sid in classes}
    found = classes | {sid for sid, s in subjects.items()
                       if s["kind"] in ("slot", "enum") and s["module"] in modules}
    return {sid: 1 for sid in found}


def machine(ctx):
    rel = ctx.wb.option("machine_definitions")
    if not rel:
        return {}
    data = load_yaml(ctx.wb.cfg.root / rel, {})
    out = {}
    for entry in (data.get("definitions") or []) if isinstance(data, dict) else []:
        sid = entry.get("subject")
        s = ctx.subjects.get(sid)
        if s and (s["payload"].get("description") or "").strip() == (entry.get("description") or "").strip():
            out[sid] = 1
    return out


BUILTIN = {"impact": impact, "adoption": adoption, "required": required, "machine": machine}
MASKED = {"port": ported}
