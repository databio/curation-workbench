"""Handlers for schema subjects: class, slot, usage and module verdicts.

Classes, slots and enums share LinkML's global namespaces, so every element is
found by name across the schema modules. Name-based references rewritten by a
rename:

  class  is_a / mixins of other classes, slot (and slot_usage) `range` /
         `any_of.range`
  slot   every class `slots:` list, `slot_usage` keys, rule `slot_conditions`
         keys, slot `is_a` / `mixins`, and the keys of example-file instances

Rules have no names; `edit_rule` / `drop_rule` address one by `rule_sha1()`,
the sha1 of its canonical JSON.

Layer discipline (when the project sets `schema.layer_annotation`): imports and
slot ranges point only down or sideways (a module's layer annotation). A handler that
moves a definition, changes a range, or changes a layer refuses when the
result has a violation the schema does not already have. The root module
(layer 0) imports everything and is exempt.
"""
import copy
import hashlib
import re
from contextlib import ExitStack, contextmanager

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from .. import store
from ..subjects import canonical
from . import ApplyError, Effects, protected
from .content import Handler as ContentHandler
from .content import (Described, example_paths, find, iter_section, module_names,
                      rename_key, set_list, slot_ranges, src, subject)

RANGE_KEYS = ("any_of", "exactly_one_of", "all_of", "none_of")
# linkml:types, which every module imports.
BUILTIN_TYPES = {"string", "integer", "boolean", "float", "double", "decimal", "time",
                 "date", "datetime", "date_or_datetime", "uriorcurie", "curie", "uri",
                 "ncname", "objectidentifier", "nodeidentifier", "jsonpointer",
                 "jsonpath", "sparqlpath"}


# ---------------------------------------------------------------- helpers

def block(obj):
    """Plain Python -> ruamel nodes in block style (the style schema files use)."""
    if isinstance(obj, dict):
        m = CommentedMap()
        for k, v in obj.items():
            m[k] = block(v)
        return m
    if isinstance(obj, (list, tuple)):
        return CommentedSeq(block(v) for v in obj)
    return obj


def rule_sha1(rule):
    """The id of a class rule: sha1 of its canonical JSON."""
    return hashlib.sha1(canonical(store.to_plain(rule)).encode("utf-8")).hexdigest()


def set_key(body, key, value, before=None):
    """Set body[key] (block style), inserting before the first of `before`, or
    drop the key when `value` is None / empty."""
    with gap(body):
        if value is None or value == [] or value == {}:
            body.pop(key, None)
        elif key in body:
            body[key] = block(value)
        else:
            keys = list(body.keys())
            pos = next((keys.index(k) for k in (before or ()) if k in keys), len(keys))
            body.insert(pos, key, block(value))


def seq_remove(seq, item=None, index=None):
    """Remove an item from a block list, handing any comment that trails it
    (ruamel attaches the comment lines after an item to that item) to the
    item before it, so removing an entry does not delete a nearby comment."""
    i = seq.index(item) if index is None else index
    trailing = seq.ca.items.pop(i, None)
    del seq[i]
    if not (trailing and trailing[0]) or i == 0:
        return
    prev = seq.ca.items.get(i - 1)
    if prev and prev[0]:
        # Both carry comment lines: the item's own end-of-line is dropped.
        prev[0].value += trailing[0].value[1:] if trailing[0].value.startswith("\n") \
            else trailing[0].value
    else:
        seq.ca.items[i - 1] = trailing


def _tail(node):
    """(ca.items, key, slot) of the comment that ends `node` (its deepest last
    scalar), or None."""
    if not isinstance(node, (CommentedMap, CommentedSeq)) or not node:
        return None
    key = list(node.keys())[-1] if isinstance(node, CommentedMap) else len(node) - 1
    inner = _tail(node[key])
    if inner:
        return inner
    return node.ca.items, key, 2 if isinstance(node, CommentedMap) else 0


def _token(tail):
    if tail is None:
        return None
    items, key, slot = tail
    return (items.get(key) or [None] * 4)[slot]


@contextmanager
def gap(container):
    """Keep the blank lines that end `container` at its end while the body of
    the `with` adds, replaces or removes its last element. ruamel stores those
    lines on the deepest last scalar, so without this they would vanish with
    it or end up in the middle of the block."""
    tok = _token(_tail(container))
    yield
    if tok is None:
        return
    body = tok.value.rstrip("\n")
    tail = tok.value[len(body):]
    if tail.count("\n") < 2:
        return
    after = _tail(container)
    atok = _token(after)
    if after is None or atok is tok:
        return
    tok.value = body + "\n"
    if atok is None:
        moved = copy.copy(tok)
        moved.value = tail
        after[0].setdefault(after[1], [None] * 4)[after[2]] = moved
    else:
        atok.value = atok.value.rstrip("\n") + tail


def keep_gap(container, mutate):
    with gap(container):
        mutate()


def seq_append(seq, value):
    keep_gap(seq, lambda: seq.append(value))


def map_add(cmap, key, value):
    """Add `key` at the end of a mapping, keeping any trailing blank lines last."""
    keep_gap(cmap, lambda: cmap.__setitem__(key, value))


def name_of(ctx, sid):
    """The current name of `sid`, following a rename staged earlier in this run
    (the registry in `ctx.subjects` is the pre-run state)."""
    s = subject(ctx, sid)
    return current(ctx, s["kind"], s["name"])


def current(ctx, kind, name):
    return getattr(ctx, "schema_renames", {}).get((kind, name), name)


def note_rename(ctx, kind, old, new):
    renames = ctx.__dict__.setdefault("schema_renames", {})
    for key, val in renames.items():
        if key[0] == kind and val == old:
            renames[key] = new
    renames[(kind, old)] = new


def cls(ctx, sid):
    """(module, body, pool) of the class `sid`, or ApplyError."""
    mod, body, pool = find(ctx, "classes", name_of(ctx, sid))
    if body is None:
        raise ApplyError(f"{sid} is not in {ctx.src}/")
    return mod, body, pool


def slot(ctx, sid):
    mod, body, pool = find(ctx, "slots", name_of(ctx, sid))
    if body is None:
        raise ApplyError(f"{sid} is not in {ctx.src}/")
    return mod, body, pool


def defined(ctx, name):
    """The section that defines `name` (classes, slots, enums, types), or None."""
    for section in ("classes", "slots", "enums", "types"):
        if find(ctx, section, name)[1] is not None:
            return section
    return None


def listers(ctx, name):
    """Classes whose `slots:` list names slot `name`."""
    return [c for _, c, body, _ in iter_section(ctx, "classes")
            if name in ((body or {}).get("slots") or [])]


def ancestors(ctx, cname):
    out, todo = [], [cname]
    while todo:
        _, body, _ = find(ctx, "classes", todo.pop())
        for p in ([body["is_a"]] if (body or {}).get("is_a") else []) + list(
                (body or {}).get("mixins") or []):
            if p not in out:
                out.append(p)
                todo.append(p)
    return out


def reaches(ctx, cname, sname):
    """True if class `cname` lists or inherits slot `sname`."""
    return any(sname in ((find(ctx, "classes", c)[1] or {}).get("slots") or [])
               for c in [cname, *ancestors(ctx, cname)])


def conditions(node):
    """Every `slot_conditions` map inside a rule (or any nested condition)."""
    out = []
    if isinstance(node, dict):
        if isinstance(node.get("slot_conditions"), dict):
            out.append(node["slot_conditions"])
        for v in node.values():
            out += conditions(v)
    elif isinstance(node, list):
        for v in node:
            out += conditions(v)
    return out


def rule_slots(ctx):
    """{slot name: [class, ...]} for slots named in any class rule."""
    out = {}
    for _, c, body, _ in iter_section(ctx, "classes"):
        for sc in conditions((body or {}).get("rules") or []):
            for s in sc:
                out.setdefault(s, []).append(c)
    return out


def example_maps(ctx):
    """(path, map) for every mapping node in the example files."""
    out = []

    def walk(path, node):
        if isinstance(node, dict):
            out.append((path, node))
            for v in node.values():
                walk(path, v)
        elif isinstance(node, list):
            for v in node:
                walk(path, v)
    for path in example_paths(ctx):
        walk(path, ctx.read_yaml(path).data)
    return out


def module_doc(ctx, mod):
    return src(ctx, mod)


def ensure_import(ctx, mod, target):
    """Make module `mod` import `target` (no-op for itself or an existing import)."""
    if mod == target:
        return
    data = module_doc(ctx, mod)
    imports = data.get("imports")
    if imports is None:
        data["imports"] = block(["linkml:types", target])
    elif target not in imports:
        seq_append(imports, target)


def home(ctx, name):
    """Module that defines class / enum / type `name`, or None (builtins)."""
    for section in ("classes", "enums", "types"):
        mod = find(ctx, section, name)[0]
        if mod:
            return mod
    return None


# ---------------------------------------------------------------- layer discipline

def layers(ctx):
    ann = ctx.wb.cfg.layer_annotation
    return {m: (module_doc(ctx, m).get("annotations") or {}).get(ann) if ann else None
            for m in module_names(ctx)}


def violations(ctx, layer=None, slot_home=None, slot_range=None, extra_imports=()):
    """Every layer-discipline violation, as strings, in the staged schema with
    optional overrides: `layer` {module: layer}, `slot_home` {slot: module},
    `slot_range` {slot: [range, ...]}, `extra_imports` [(module, target)]."""
    if not ctx.wb.cfg.layer_annotation:
        return set()
    lay = layers(ctx) | (layer or {})
    out = set()
    edges = [(m, t) for m in module_names(ctx)
             for t in module_doc(ctx, m).get("imports") or [] if t in lay]
    for m, t in list(edges) + list(extra_imports):
        if lay[m] and lay[t] > lay[m]:
            out.add(f"module {m} (layer {lay[m]}) imports {t} (layer {lay[t]})")
    for mod, s, body, _ in iter_section(ctx, "slots"):
        mod = (slot_home or {}).get(s, mod)
        for rng in (slot_range or {}).get(s, slot_ranges(body)):
            rmod = home(ctx, rng)
            if rmod and lay[mod] and lay[rmod] > lay[mod]:
                out.add(f"slot {s} (layer {lay[mod]}) ranges on {rng} (layer {lay[rmod]})")
    return out


def new_violations(ctx, **override):
    return sorted(violations(ctx, **override) - violations(ctx))


# ---------------------------------------------------------------- base handler

class Handler(ContentHandler):
    """content.Handler, plus: every class / slot body, section and module keeps
    the blank lines that end it, whatever the handler adds, replaces or drops."""

    def apply(self, rec, ctx):
        with ExitStack() as stack:
            for mod in module_names(ctx):
                data = src(ctx, mod)
                for section in ("classes", "slots"):
                    pool = data.get(section)
                    if isinstance(pool, CommentedMap):
                        for body in pool.values():
                            if isinstance(body, CommentedMap):
                                stack.enter_context(gap(body))
                        stack.enter_context(gap(pool))
                stack.enter_context(gap(data))
            return super().apply(rec, ctx)


# ---------------------------------------------------------------- mappings (class + slot)

class Mapping(Handler):
    """adopt_mapping / reject_mapping on a class or slot: its *_mappings list."""

    def __init__(self, kind, verdict, adopt):
        self.kind, self.verdict, self.adopt = kind, verdict, adopt

    def body(self, rec, ctx):
        return (cls if self.kind == "class" else slot)(ctx, rec["subject"])[1]

    def check(self, rec, ctx):
        op = self.op(rec)
        have = [str(x) for x in self.body(rec, ctx).get(op["predicate"]) or []]
        if self.adopt and op["object"] in have:
            return [f"{op['predicate']} already has {op['object']}"]
        return []

    def run(self, rec, ctx):
        op = self.op(rec)
        body = self.body(rec, ctx)
        have = [str(x) for x in body.get(op["predicate"]) or []]
        if self.adopt:
            set_list(body, op["predicate"], have + [op["object"]],
                     before=("slots", "slot_usage", "rules", "range"))
        else:
            # Absent already: the record still documents the rejection.
            set_list(body, op["predicate"], [x for x in have if x != op["object"]])


# ---------------------------------------------------------------- class verdicts

class ClassRename(Handler):
    kind, verdict = "class", "rename_class"

    def check(self, rec, ctx):
        old, new = name_of(ctx, rec["subject"]), self.op(rec)["name"]
        cls(ctx, rec["subject"])
        errs = []
        why = protected(ctx, "classes").get(old)
        if why:
            errs.append(f"{old} is protected ({why}); change that first")
        if defined(ctx, new):
            errs.append(f"{new} is already defined")
        return errs

    def run(self, rec, ctx):
        old, new = name_of(ctx, rec["subject"]), self.op(rec)["name"]
        _, body, pool = cls(ctx, rec["subject"])
        for _, _, b, _ in iter_section(ctx, "classes"):
            b = b or {}
            if b.get("is_a") == old:
                b["is_a"] = new
            if old in (b.get("mixins") or []):
                b["mixins"][b["mixins"].index(old)] = new
            for u in (b.get("slot_usage") or {}).values():
                retarget_range(u, old, new)
        for _, _, b, _ in iter_section(ctx, "slots"):
            retarget_range(b, old, new)
        rename_key(pool, old, new)
        note_rename(ctx, "class", old, new)
        new_sid = f"class:{new}"
        renamed = {rec["subject"]: new_sid}
        created = [new_sid]
        for s in body.get("slot_usage") or {}:
            renamed[f"usage:{old}/{s}"] = f"usage:{new}/{s}"
            created.append(f"usage:{new}/{s}")
        return Effects(renamed=renamed, created=created)


def retarget_range(body, old, new):
    body = body or {}
    if body.get("range") == old:
        body["range"] = new
    for key in RANGE_KEYS:
        for b in body.get(key) or []:
            if (b or {}).get("range") == old:
                b["range"] = new


class ClassAddSlot(Handler):
    kind, verdict = "class", "add_slot"

    def check(self, rec, ctx):
        op = self.op(rec)
        mod, body, _ = cls(ctx, rec["subject"])
        if op.get("slot"):
            name = name_of(ctx, op["slot"])
            if find(ctx, "slots", name)[1] is None:
                return [f"{op['slot']} is not in src/"]
            if name in (body.get("slots") or []):
                return [f"{rec['subject']} already lists {name}"]
            return []
        new = dict(op["new_slot"])
        name = new.pop("name", None)
        if not isinstance(name, str) or not re.match(r"^[a-z][a-z0-9_]*$", name):
            return ["operation.new_slot.name must be a snake_case slot name"]
        if defined(ctx, name):
            return [f"{name} is already defined"]
        errs = [f"new_slot range {r!r} is not a class, enum or type" for r in slot_ranges(new)
                if r not in BUILTIN_TYPES and not home(ctx, r)]
        return errs + new_violations(ctx, slot_home={name: mod},
                                     slot_range={name: slot_ranges(new)},
                                     extra_imports=[(mod, home(ctx, r)) for r in slot_ranges(new)
                                                    if home(ctx, r)])

    def run(self, rec, ctx):
        op = self.op(rec)
        mod, body, _ = cls(ctx, rec["subject"])
        if op.get("slot"):
            name = name_of(ctx, op["slot"])
            created = []
        else:
            new = dict(op["new_slot"])
            name = new.pop("name")
            data = src(ctx, mod)
            if data.get("slots") is None:
                data["slots"] = CommentedMap()
            map_add(data["slots"], name, block(new))
            for r in slot_ranges(new):
                if home(ctx, r):
                    ensure_import(ctx, mod, home(ctx, r))
            created = [f"slot:{name}"]
        if body.get("slots") is None:
            set_key(body, "slots", [name], before=("slot_usage", "rules"))
        else:
            seq_append(body["slots"], name)
        return Effects(created=created)


class ClassDropSlot(Handler):
    kind, verdict = "class", "drop_slot"

    def check(self, rec, ctx):
        cname = name_of(ctx, rec["subject"])
        _, body, _ = cls(ctx, rec["subject"])
        sname = name_of(ctx, self.op(rec)["slot"])
        errs = []
        if sname not in (body.get("slots") or []):
            errs.append(f"{cname} does not list {sname}")
        if sname in (body.get("slot_usage") or {}):
            errs.append(f"{cname} has a slot_usage for {sname}; drop_usage first")
        if self.op(rec).get("delete_def"):
            others = [c for c in listers(ctx, sname) if c != cname]
            if others:
                errs.append(f"cannot delete the {sname} definition: still listed by "
                            f"{', '.join(others)}")
            errs += slot_refs(ctx, sname, skip_class=cname)
        return errs

    def run(self, rec, ctx):
        _, body, _ = cls(ctx, rec["subject"])
        sname = name_of(ctx, self.op(rec)["slot"])
        seq_remove(body["slots"], sname)
        if not body["slots"]:
            body.pop("slots")
        if self.op(rec).get("delete_def"):
            delete_slot_def(ctx, sname)
            return Effects(retired=[self.op(rec)["slot"]])


def slot_refs(ctx, sname, skip_class=None):
    """Why slot `sname`'s definition cannot go: slot_usage entries, rule
    conditions and example keys that still name it."""
    errs = []
    for _, c, body, _ in iter_section(ctx, "classes"):
        if c != skip_class and sname in ((body or {}).get("slot_usage") or {}):
            errs.append(f"class {c} has a slot_usage for {sname}")
    for c in rule_slots(ctx).get(sname, []):
        errs.append(f"a rule on class {c} names {sname}")
    for path in sorted({p for p, m in example_maps(ctx) if sname in m}):
        errs.append(f"{path} uses {sname}")
    return errs


def delete_slot_def(ctx, sname):
    mod, _, pool = find(ctx, "slots", sname)
    pool.pop(sname)
    if not pool:
        src(ctx, mod).pop("slots")


class ClassMoveSlot(Handler):
    kind, verdict = "class", "move_slot"

    def plan(self, rec, ctx):
        """(slot name, target class, target module, def module after the move)."""
        sname = name_of(ctx, self.op(rec)["slot"])
        tname = name_of(ctx, self.op(rec)["to"])
        smod, _, _ = cls(ctx, rec["subject"])
        tmod, _, _ = cls(ctx, self.op(rec)["to"])
        dmod = find(ctx, "slots", sname)[0]
        others = [c for c in listers(ctx, sname) if c != name_of(ctx, rec["subject"])]
        new_home = tmod if (dmod == smod and not others) else dmod
        return sname, tname, tmod, dmod, new_home

    def check(self, rec, ctx):
        cname = name_of(ctx, rec["subject"])
        _, body, _ = cls(ctx, rec["subject"])
        _, tbody, _ = cls(ctx, self.op(rec)["to"])
        sname, tname, tmod, dmod, new_home = self.plan(rec, ctx)
        errs = []
        if tname == cname:
            errs.append("a slot cannot move to the class that already lists it")
        if sname not in (body.get("slots") or []):
            errs.append(f"{cname} does not list {sname}")
        if sname in (tbody.get("slots") or []):
            errs.append(f"{tname} already lists {sname}")
        if sname in (body.get("slot_usage") or {}):
            errs.append(f"{cname} has a slot_usage for {sname}; drop_usage first")
        if new_home != dmod:
            _, sbody, _ = find(ctx, "slots", sname)
            errs += new_violations(ctx, slot_home={sname: new_home}, extra_imports=[
                (new_home, home(ctx, r)) for r in slot_ranges(sbody) if home(ctx, r)])
        return errs

    def run(self, rec, ctx):
        _, body, _ = cls(ctx, rec["subject"])
        _, tbody, _ = cls(ctx, self.op(rec)["to"])
        sname, tname, tmod, dmod, new_home = self.plan(rec, ctx)
        seq_remove(body["slots"], sname)
        if not body["slots"]:
            body.pop("slots")
        if tbody.get("slots") is None:
            set_key(tbody, "slots", [sname], before=("slot_usage", "rules"))
        else:
            seq_append(tbody["slots"], sname)
        if new_home != dmod:
            _, sbody, pool = find(ctx, "slots", sname)
            comment = pool.ca.items.pop(sname, None)
            delete_slot_def(ctx, sname)
            data = src(ctx, new_home)
            if data.get("slots") is None:
                data["slots"] = CommentedMap()
            map_add(data["slots"], sname, sbody)
            if comment is not None:
                data["slots"].ca.items[sname] = comment
            for r in slot_ranges(sbody):
                if home(ctx, r):
                    ensure_import(ctx, new_home, home(ctx, r))


class ClassSetSlotUsage(Handler):
    kind, verdict = "class", "set_slot_usage"

    def check(self, rec, ctx):
        cname = name_of(ctx, rec["subject"])
        cls(ctx, rec["subject"])
        sname = name_of(ctx, self.op(rec)["slot"])
        if not reaches(ctx, cname, sname):
            return [f"{cname} neither lists nor inherits {sname}"]
        if not self.op(rec)["usage"]:
            return ["operation.usage is empty; use drop_usage to remove an override"]
        return []

    def run(self, rec, ctx):
        cname = name_of(ctx, rec["subject"])
        _, body, _ = cls(ctx, rec["subject"])
        sname = name_of(ctx, self.op(rec)["slot"])
        usage = body.get("slot_usage")
        if usage is None:
            set_key(body, "slot_usage", {sname: self.op(rec)["usage"]}, before=("rules",))
            usage = body["slot_usage"]
            new = True
        else:
            new = sname not in usage
            usage[sname] = block(self.op(rec)["usage"])
        return Effects(created=[f"usage:{cname}/{sname}"] if new else [])


class ClassRule(Handler):
    """add_rule / edit_rule / drop_rule; a rule is addressed by rule_sha1()."""
    kind = "class"

    def __init__(self, verdict):
        self.verdict = verdict

    def rules(self, rec, ctx):
        return cls(ctx, rec["subject"])[1].get("rules") or []

    def index(self, rec, ctx):
        want = self.op(rec)["rule_sha1"]
        hits = [i for i, r in enumerate(self.rules(rec, ctx)) if rule_sha1(r) == want]
        return hits[0] if hits else None

    def check(self, rec, ctx):
        cname = name_of(ctx, rec["subject"])
        have = {rule_sha1(r) for r in self.rules(rec, ctx)}
        errs = []
        if self.verdict != "add_rule" and self.index(rec, ctx) is None:
            errs.append(f"{cname} has no rule with sha1 {self.op(rec)['rule_sha1']}")
        if self.verdict != "drop_rule":
            rule = self.op(rec)["rule"]
            if rule_sha1(rule) in have:
                errs.append(f"{cname} already has this rule")
            for s in {s for sc in conditions(rule) for s in sc}:
                if not reaches(ctx, cname, s):
                    errs.append(f"rule names slot {s}, which {cname} neither lists nor inherits")
        return errs

    def run(self, rec, ctx):
        _, body, _ = cls(ctx, rec["subject"])
        if self.verdict == "add_rule":
            if body.get("rules") is None:
                set_key(body, "rules", [self.op(rec)["rule"]])
            else:
                seq_append(body["rules"], block(self.op(rec)["rule"]))
            return
        i = self.index(rec, ctx)
        if self.verdict == "edit_rule":
            body["rules"][i] = block(self.op(rec)["rule"])
        else:
            seq_remove(body["rules"], index=i)
            if not body["rules"]:
                body.pop("rules")


class ClassSetIsA(Handler):
    kind, verdict = "class", "set_is_a"

    def check(self, rec, ctx):
        cname = name_of(ctx, rec["subject"])
        mod, body, _ = cls(ctx, rec["subject"])
        parent = self.op(rec)["is_a"]
        if parent is None:
            return [] if body.get("is_a") else [f"{cname} has no is_a"]
        pname = name_of(ctx, parent)
        pmod, _, _ = cls(ctx, parent)
        if pname == body.get("is_a"):
            return [f"{cname} already is_a {pname}"]
        if pname == cname or cname in ancestors(ctx, pname):
            return [f"{pname} descends from {cname}; is_a would make a cycle"]
        return new_violations(ctx, extra_imports=[(mod, pmod)])

    def run(self, rec, ctx):
        mod, body, _ = cls(ctx, rec["subject"])
        parent = self.op(rec)["is_a"]
        if parent is None:
            body.pop("is_a")
            return
        pname = name_of(ctx, parent)
        set_key(body, "is_a", pname, before=("mixins", "slots", "slot_usage", "rules"))
        ensure_import(ctx, mod, find(ctx, "classes", pname)[0])


class ClassSetLineage(Handler):
    kind, verdict = "class", "set_lineage"

    def run(self, rec, ctx):
        _, body, _ = cls(ctx, rec["subject"])
        for key in ("source", "conforms_to", "see_also"):
            if key in self.op(rec):
                set_key(body, key, self.op(rec)[key], before=("slots", "slot_usage", "rules"))


class ClassDelete(Handler):
    kind, verdict = "class", "delete_class"

    def check(self, rec, ctx):
        cname = name_of(ctx, rec["subject"])
        cls(ctx, rec["subject"])
        errs = []
        why = protected(ctx, "classes").get(cname)
        if why:
            errs.append(f"{cname} is protected ({why})")
        for _, c, b, _ in iter_section(ctx, "classes"):
            b = b or {}
            if b.get("is_a") == cname or cname in (b.get("mixins") or []):
                errs.append(f"class {c} inherits from {cname}")
            for s, u in (b.get("slot_usage") or {}).items():
                if cname in slot_ranges(u):
                    errs.append(f"slot_usage {c}.{s} ranges on {cname}")
        for _, s, b, _ in iter_section(ctx, "slots"):
            if cname in slot_ranges(b):
                errs.append(f"slot {s} ranges on {cname}")
        return [f"{rec['subject']} is still referenced: " + "; ".join(errs)] if errs else []

    def run(self, rec, ctx):
        cname = name_of(ctx, rec["subject"])
        mod, body, pool = cls(ctx, rec["subject"])
        usages = [f"usage:{cname}/{s}" for s in body.get("slot_usage") or {}]
        pool.pop(cname)
        if not pool:
            src(ctx, mod).pop("classes")
        return Effects(retired=[rec["subject"], *usages])


# ---------------------------------------------------------------- slot verdicts

class SlotRename(Handler):
    kind, verdict = "slot", "rename_slot"

    def check(self, rec, ctx):
        slot(ctx, rec["subject"])
        new = self.op(rec)["name"]
        if not re.match(r"^[a-z][a-z0-9_]*$", new):
            return [f"{new!r} is not a snake_case slot name"]
        return [f"{new} is already defined"] if defined(ctx, new) else []

    def run(self, rec, ctx):
        old, new = name_of(ctx, rec["subject"]), self.op(rec)["name"]
        _, body, pool = slot(ctx, rec["subject"])
        renamed = {rec["subject"]: f"slot:{new}"}
        for _, c, b, _ in iter_section(ctx, "classes"):
            b = b or {}
            if old in (b.get("slots") or []):
                b["slots"][b["slots"].index(old)] = new
            if old in (b.get("slot_usage") or {}):
                rename_key(b["slot_usage"], old, new)
                renamed[f"usage:{c}/{old}"] = f"usage:{c}/{new}"
            for sc in conditions(b.get("rules") or []):
                if old in sc:
                    rename_key(sc, old, new)
        for _, _, b, _ in iter_section(ctx, "slots"):
            b = b or {}
            if b.get("is_a") == old:
                b["is_a"] = new
            if old in (b.get("mixins") or []):
                b["mixins"][b["mixins"].index(old)] = new
        for _, m in example_maps(ctx):
            if old in m:
                rename_key(m, old, new)
        rename_key(pool, old, new)
        note_rename(ctx, "slot", old, new)
        if body.get("name") == old:
            body["name"] = new
        return Effects(renamed=renamed, created=list(renamed.values()))


class SlotRetype(Handler):
    kind, verdict = "slot", "retype_slot"

    def after(self, rec, body):
        """The slot's ranges once the operation applies."""
        op = self.op(rec)
        rng = op["range"] if "range" in op else body.get("range")
        branches = op["any_of"] if "any_of" in op else body.get("any_of") or []
        out = [rng] if rng else []
        out += [b["range"] for b in branches if (b or {}).get("range")]
        for key in RANGE_KEYS[1:]:
            out += [b["range"] for b in body.get(key) or [] if (b or {}).get("range")]
        return out

    def check(self, rec, ctx):
        sname = name_of(ctx, rec["subject"])
        mod, body, _ = slot(ctx, rec["subject"])
        op = self.op(rec)
        errs = []
        for b in op.get("any_of") or []:
            if not isinstance(b, dict) or not b.get("range"):
                errs.append("every any_of branch needs a range")
        ranges = self.after(rec, body)
        errs += [f"range {r!r} is not a class, enum or type" for r in ranges
                 if r not in BUILTIN_TYPES and not home(ctx, r)]
        if errs:
            return errs
        return new_violations(ctx, slot_range={sname: ranges},
                              extra_imports=[(mod, home(ctx, r)) for r in ranges if home(ctx, r)])

    def run(self, rec, ctx):
        mod, body, _ = slot(ctx, rec["subject"])
        op = self.op(rec)
        if "range" in op:
            set_key(body, "range", op["range"], before=("inlined", "examples"))
        if "any_of" in op:
            set_key(body, "any_of", op["any_of"], before=("examples",))
        if "inlined" in op:
            set_key(body, "inlined", op["inlined"] or None, before=("examples",))
        for r in self.after(rec, body):
            if home(ctx, r):
                ensure_import(ctx, mod, home(ctx, r))


class SlotCardinality(Handler):
    kind, verdict = "slot", "set_cardinality"

    def check(self, rec, ctx):
        _, body, _ = slot(ctx, rec["subject"])
        op = self.op(rec)
        final = {k: op.get(k, bool(body.get(k))) for k in ("required", "recommended")}
        if final["required"] and final["recommended"]:
            return ["a slot cannot be both required and recommended"]
        if all(bool(body.get(k)) == v for k, v in op.items() if k != "op"):
            return [f"{rec['subject']} already has that cardinality"]
        return []

    def run(self, rec, ctx):
        _, body, _ = slot(ctx, rec["subject"])
        for key in ("required", "recommended", "multivalued"):
            if key in self.op(rec):
                set_key(body, key, True if self.op(rec)[key] else None,
                        before=("range", "examples"))


class SlotExamples(Handler):
    kind, verdict = "slot", "set_examples"

    def check(self, rec, ctx):
        slot(ctx, rec["subject"])
        bad = [e for e in self.op(rec)["examples"] if not isinstance(e, dict) or "value" not in e]
        return ["every example needs a `value`"] if bad else []

    def run(self, rec, ctx):
        _, body, _ = slot(ctx, rec["subject"])
        set_key(body, "examples", self.op(rec)["examples"])


class SlotDelete(Handler):
    kind, verdict = "slot", "delete_slot"

    def check(self, rec, ctx):
        sname = name_of(ctx, rec["subject"])
        slot(ctx, rec["subject"])
        refs = [r for r in slot_refs(ctx, sname) if "slot_usage" not in r]
        return [f"{rec['subject']} is still referenced: " + "; ".join(refs)] if refs else []

    def run(self, rec, ctx):
        sname = name_of(ctx, rec["subject"])
        retired = [rec["subject"]]
        for _, c, b, _ in iter_section(ctx, "classes"):
            b = b or {}
            if sname in (b.get("slots") or []):
                seq_remove(b["slots"], sname)
                if not b["slots"]:
                    b.pop("slots")
            if sname in (b.get("slot_usage") or {}):
                b["slot_usage"].pop(sname)
                retired.append(f"usage:{c}/{sname}")
                if not b["slot_usage"]:
                    b.pop("slot_usage")
        delete_slot_def(ctx, sname)
        return Effects(retired=retired)


# ---------------------------------------------------------------- usage verdicts

class Usage(Handler):
    kind = "usage"

    def __init__(self, verdict):
        self.verdict = verdict

    def where(self, rec, ctx):
        s = subject(ctx, rec["subject"])
        _, body, _ = find(ctx, "classes", current(ctx, "class", s["owner_class"]))
        sname = current(ctx, "slot", s["name"])
        if sname not in ((body or {}).get("slot_usage") or {}):
            raise ApplyError(f"{rec['subject']} is not in {ctx.src}/")
        return body, sname

    def check(self, rec, ctx):
        self.where(rec, ctx)
        if self.verdict == "edit_usage" and not self.op(rec)["usage"]:
            return ["operation.usage is empty; use drop_usage"]
        return []

    def run(self, rec, ctx):
        body, sname = self.where(rec, ctx)
        if self.verdict == "edit_usage":
            body["slot_usage"][sname] = block(self.op(rec)["usage"])
            return
        body["slot_usage"].pop(sname)
        if not body["slot_usage"]:
            body.pop("slot_usage")
        return Effects(retired=[rec["subject"]])


# ---------------------------------------------------------------- module verdicts

class ModuleEditDescription(Handler):
    kind, verdict = "module", "edit_description"

    def run(self, rec, ctx):
        data = src(ctx, name_of(ctx, rec["subject"]))
        set_key(data, "description", self.op(rec)["description"],
                before=("annotations", "prefixes", "imports"))


class ModuleSetLayer(Handler):
    kind, verdict = "module", "set_layer"

    def check(self, rec, ctx):
        mod, layer = name_of(ctx, rec["subject"]), self.op(rec)["layer"]
        if layers(ctx)[mod] == layer:
            return [f"{mod} is already layer {layer}"]
        lo, hi = ctx.wb.option("layer_range", [1, 4])
        if not lo <= layer <= hi:
            return [f"layer must be {lo}-{hi} (0 is the root module)"]
        bad = new_violations(ctx, layer={mod: layer})
        return [f"layer {layer} for {mod} breaks layer discipline: " + "; ".join(bad)] \
            if bad else []

    def run(self, rec, ctx):
        data = src(ctx, name_of(ctx, rec["subject"]))
        data["annotations"][ctx.wb.cfg.layer_annotation] = self.op(rec)["layer"]


# ---------------------------------------------------------------- registration


def handlers():
    """Every generic schema handler instance."""
    return [
        Described("class", "classes"),
        ClassRename(),
        ClassAddSlot(),
        ClassDropSlot(),
        ClassMoveSlot(),
        ClassSetSlotUsage(),
        ClassRule("add_rule"),
        ClassRule("edit_rule"),
        ClassRule("drop_rule"),
        ClassSetIsA(),
        ClassSetLineage(),
        Mapping("class", "adopt_mapping", adopt=True),
        Mapping("class", "reject_mapping", adopt=False),
        ClassDelete(),
        Described("slot", "slots"),
        SlotRename(),
        SlotRetype(),
        SlotCardinality(),
        SlotExamples(),
        Mapping("slot", "adopt_mapping", adopt=True),
        Mapping("slot", "reject_mapping", adopt=False),
        SlotDelete(),
        Usage("edit_usage"),
        Usage("drop_usage"),
        ModuleEditDescription(),
        ModuleSetLayer(),
    ]
