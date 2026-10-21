"""Handlers for content subjects: term, enum and subset verdicts.

Terms are addressed by id (`meaning: <prefix>:<local id>`), never by label, so a
rename or a move between enums keeps every id-based reference (SSSOM
`subject_id`, `see_also`) intact. Label-based references are found and
rewritten in the same transaction by the reference providers in
`wb.ref_providers`, in order. Builtin providers:

  see_also      `see_also` of other permissible values (by id; find only)
  sssom         rows of the SSSOM files (by id; `subject_label` is rewritten
                by rename itself)
  examples      values of slots whose range is the enum, in the examples dir
                (option `examples_dir`, default `examples`) and in schema slot
                `examples:`

A project adds providers for its own label-keyed files: an object with
`find(ctx, loc) -> [str]`, `relabel(ctx, loc, new_label, new_enum)` and
`enum_renamed(ctx, old, new)` (any may be absent).

Retiring a term (merge, delete) records it in the ledger with the decision id;
its id is never reused.
"""
import re

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from . import ApplyError, Effects, extra_checks, protected

MANUAL = "semapv:ManualMappingCuration"


# ---------------------------------------------------------------- schema access

def module_names(ctx):
    cfg = ctx.wb.cfg
    return sorted(p.stem for p in cfg.schema_dir.glob("*.yaml")
                  if p.name not in cfg.schema_exclude)


def src(ctx, mod):
    return ctx.read_yaml(f"{ctx.src}/{mod}.yaml").data


def iter_section(ctx, section):
    """(module, name, body, container map) for every element of a section."""
    for mod in module_names(ctx):
        pool = src(ctx, mod).get(section) or {}
        for name in list(pool):
            yield mod, name, pool[name], pool


def find(ctx, section, name):
    for mod, n, body, pool in iter_section(ctx, section):
        if n == name:
            return mod, body, pool
    return None, None, None


class TermLoc:
    def __init__(self, mod, enum, label, pv, pvs):
        self.mod, self.enum, self.label, self.pv, self.pvs = mod, enum, label, pv, pvs


def find_term(ctx, tid):
    meaning = ctx.meaning(tid)
    for mod, ename, body, _ in iter_section(ctx, "enums"):
        pvs = (body or {}).get("permissible_values") or {}
        for label, pv in pvs.items():
            if pv and str(pv.get("meaning")) == meaning:
                return TermLoc(mod, ename, label, pv, pvs)
    return None


def all_values(ctx):
    for mod, ename, body, _ in iter_section(ctx, "enums"):
        for label, pv in ((body or {}).get("permissible_values") or {}).items():
            yield ename, label, pv


def subject(ctx, sid):
    s = ctx.subjects.get(sid)
    if s is None:
        raise ApplyError(f"unknown subject {sid}")
    return s


def tid_of(ctx, sid):
    return subject(ctx, sid)["term_id"]


def rename_key(cmap, old, new):
    """Rename a mapping key in place, keeping its position and comments."""
    keys = list(cmap.keys())
    pos = keys.index(old)
    comment = cmap.ca.items.pop(old, None)
    value = cmap.pop(old)
    cmap.insert(pos, new, value)
    if comment is not None:
        cmap.ca.items[new] = comment


def set_list(pv, key, values, before=None):
    """Set pv[key] to a block list, or drop the key when `values` is empty."""
    if not values:
        pv.pop(key, None)
        return
    if key in pv and isinstance(pv[key], list):
        pv[key].clear()
        pv[key].extend(values)
        return
    seq = CommentedSeq(values)
    keys = list(pv.keys())
    pos = next((keys.index(k) for k in (before or ()) if k in keys), len(keys))
    pv.insert(pos, key, seq)


def slot_ranges(body):
    body = body or {}
    out = [body["range"]] if body.get("range") else []
    for key in ("any_of", "exactly_one_of", "all_of", "none_of"):
        out += [b["range"] for b in body.get(key) or [] if (b or {}).get("range")]
    return out


def slots_ranging_on(ctx, name):
    return [(mod, s, body) for mod, s, body, _ in iter_section(ctx, "slots")
            if name in slot_ranges(body)]


def ledger_row(ctx, tid):
    for r in ctx.read_tsv(ctx.ledger).rows:
        if r["id"] == tid:
            return r
    raise ApplyError(f"{tid} is not in {ctx.ledger}")


def retire(ctx, tid, status, enum, label, decision, replaced_by=""):
    row = ledger_row(ctx, tid)
    row.update(status=status, enum=enum, label=label, replaced_by=replaced_by, decision=decision)


def label_taken(ctx, label, tid=None):
    """Why `label` cannot be given to term `tid`, or None. Labels are unique
    across every id ever minted (the crosswalk maps each to one id)."""
    for ename, lab, pv in all_values(ctx):
        if lab == label and str((pv or {}).get("meaning")) != ctx.meaning(tid):
            return f"label {label!r} is already used by {ename}"
    for r in ctx.read_tsv(ctx.ledger).rows:
        if r["id"] == tid:
            continue
        if r.get("label") == label or label in (r.get("former_labels") or "").split("|"):
            return f"label {label!r} was used by {r['id']} (ledger)"
    return None


def _tid(loc):
    return str(loc.pv["meaning"]).split(":", 1)[1]


# ---------------------------------------------------------------- label references

def example_paths(ctx):
    d = ctx.wb.option("examples_dir", "examples")
    return sorted(f"{d}/{p.name}" for p in (ctx.root / d).glob("*.yaml"))


def example_sites(ctx, enum):
    """(container, key-or-index) for every example value of a slot ranging on `enum`."""
    slots = {s for _, s, _ in slots_ranging_on(ctx, enum)}
    sites = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in slots:
                    if isinstance(v, str):
                        sites.append((node, k))
                    elif isinstance(v, list):
                        sites.extend((v, i) for i, x in enumerate(v) if isinstance(x, str))
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    for path in example_paths(ctx):
        walk(ctx.read_yaml(path).data)
    for _, _, body in slots_ranging_on(ctx, enum):
        for ex in (body or {}).get("examples") or []:
            if isinstance(ex, dict) and isinstance(ex.get("value"), str):
                sites.append((ex, "value"))
    return sites


class SeeAlsoRefs:
    name = "see_also"

    def find(self, ctx, loc):
        meaning = ctx.meaning(_tid(loc))
        return [f"see_also of {ename} {lab!r}" for ename, lab, pv in all_values(ctx)
                if meaning in [str(x) for x in (pv or {}).get("see_also") or []]]


class SssomRefs:
    name = "sssom"

    def find(self, ctx, loc):
        meaning, out = ctx.meaning(_tid(loc)), []
        for path in sssom_paths(ctx):
            n = sum(1 for r in ctx.read_tsv(path).rows if r["subject_id"] == meaning)
            if n:
                out.append(f"{n} row(s) in {path}")
        return out


class ExampleRefs:
    name = "examples"

    def find(self, ctx, loc):
        return [f"example value {loc.label!r}" for container, key in example_sites(ctx, loc.enum)
                if container[key] == loc.label]

    def relabel(self, ctx, loc, new_label, new_enum):
        if new_enum == loc.enum:
            for container, key in example_sites(ctx, loc.enum):
                if container[key] == loc.label:
                    container[key] = new_label


def references(ctx, loc):
    """Every label- or id-based reference to a term, as human-readable strings."""
    out = []
    for p in ctx.wb.ref_providers:
        if hasattr(p, "find"):
            out += p.find(ctx, loc)
    return out


def relabel(ctx, loc, new_label, new_enum=None):
    """Point every label-based reference to `loc` at `new_label` (and, for a
    move, `new_enum`). SSSOM rows are matched by id."""
    new_enum = new_enum or loc.enum
    for p in ctx.wb.ref_providers:
        if hasattr(p, "relabel"):
            p.relabel(ctx, loc, new_label, new_enum)


def enum_renamed(ctx, old, new):
    for p in ctx.wb.ref_providers:
        if hasattr(p, "enum_renamed"):
            p.enum_renamed(ctx, old, new)


# ---------------------------------------------------------------- SSSOM

def sssom_paths(ctx):
    return sorted(ctx.wb.cfg.rel(p) for p in ctx.wb.cfg.sssom_files())


def sssom_path_for(ctx, obj):
    """The SSSOM file for an object's prefix: `<mappings dir>/<prefix>.sssom.tsv`."""
    prefix = obj.split(":", 1)[0].lower() if ":" in obj else ""
    glob = ctx.wb.cfg.sssom_glob
    return glob.replace("*", prefix, 1)


def sssom_relabel(ctx, tid, new_label):
    for path in sssom_paths(ctx):
        for r in ctx.read_tsv(path).rows:
            if r["subject_id"] == ctx.meaning(tid):
                r["subject_label"] = new_label


# ---------------------------------------------------------------- base handler

class Handler:
    kind = verdict = None

    def op(self, rec):
        return rec.get("operation") or {}

    def term(self, ctx, rec, sid=None):
        loc = find_term(ctx, tid_of(ctx, sid or rec["subject"]))
        if loc is None:
            raise ApplyError(f"{sid or rec['subject']} is not a live value in {ctx.src}/")
        return loc

    def check(self, rec, ctx):
        """Extra refusals; [] if fine."""
        return []

    def preflight(self, rec, ctx):
        try:
            if self.kind == "term":
                self.term(ctx, rec)
            return self.check(rec, ctx)
        except ApplyError as e:
            return [str(e)]

    def apply(self, rec, ctx):
        problems = self.preflight(rec, ctx)
        if problems:
            raise ApplyError("; ".join(problems))
        return self.run(rec, ctx) or Effects()


# ---------------------------------------------------------------- term verdicts

class TermEditDescription(Handler):
    kind, verdict = "term", "edit_description"

    def run(self, rec, ctx):
        loc = self.term(ctx, rec)
        if "description" in loc.pv:
            loc.pv["description"] = self.op(rec)["description"]
        else:
            loc.pv.insert(0, "description", self.op(rec)["description"])


class TermSetSubsets(Handler):
    kind, verdict = "term", "set_subsets"

    def run(self, rec, ctx):
        names = [subject(ctx, s)["name"] for s in self.op(rec)["subsets"]]
        set_list(self.term(ctx, rec).pv, "in_subset", names, before=("keywords", "see_also"))


class TermSeeAlso(Handler):
    kind = "term"

    def __init__(self, verdict, add):
        self.verdict, self.add = verdict, add

    def check(self, rec, ctx):
        target = self.op(rec)["target"]
        if target == rec["subject"]:
            return ["a term cannot see_also itself"]
        have = [str(x) for x in self.term(ctx, rec).pv.get("see_also") or []]
        ref = ctx.meaning(tid_of(ctx, target))
        if self.add and ref in have:
            return [f"see_also already has {ref}"]
        if not self.add and ref not in have:
            return [f"see_also has no {ref}"]
        return []

    def run(self, rec, ctx):
        pv = self.term(ctx, rec).pv
        ref = ctx.meaning(tid_of(ctx, self.op(rec)['target']))
        have = [str(x) for x in pv.get("see_also") or []]
        set_list(pv, "see_also", have + [ref] if self.add else [x for x in have if x != ref])


class TermRename(Handler):
    kind, verdict = "term", "rename"

    def check(self, rec, ctx):
        loc, new = self.term(ctx, rec), self.op(rec)["label"]
        if new == loc.label:
            return [f"{rec['subject']} is already labelled {new!r}"]
        taken = label_taken(ctx, new, _tid(loc))
        return [taken] if taken else []

    def run(self, rec, ctx):
        loc, new = self.term(ctx, rec), self.op(rec)["label"]
        tid, old = _tid(loc), loc.label
        relabel(ctx, loc, new)
        sssom_relabel(ctx, tid, new)
        rename_key(loc.pvs, old, new)
        row = ledger_row(ctx, tid)
        former = [x for x in (row.get("former_labels") or "").split("|") if x]
        row["former_labels"] = "|".join(former + [old])
        row["decision"] = rec["id"]


class TermMoveToEnum(Handler):
    kind, verdict = "term", "move_to_enum"

    def check(self, rec, ctx):
        loc = self.term(ctx, rec)
        target = subject(ctx, self.op(rec)["enum"])["name"]
        errs = []
        if target == loc.enum:
            errs.append(f"{rec['subject']} is already in {target}")
        tmod, tbody, _ = find(ctx, "enums", target)
        if tbody is None:
            errs.append(f"enum {target} is not in {ctx.src}/")
        elif loc.label in (tbody.get("permissible_values") or {}):
            errs.append(f"{target} already has a value {loc.label!r}")
        errs += extra_checks(self, rec, ctx)
        if any(c[k] == loc.label for c, k in example_sites(ctx, loc.enum)):
            errs.append(f"example files use {loc.label!r} as a {loc.enum} value")
        return errs

    def run(self, rec, ctx):
        loc = self.term(ctx, rec)
        target = subject(ctx, self.op(rec)["enum"])["name"]
        _, tbody, _ = find(ctx, "enums", target)
        relabel(ctx, loc, loc.label, new_enum=target)
        comment = loc.pvs.ca.items.pop(loc.label, None)
        pv = loc.pvs.pop(loc.label)
        tpvs = tbody.setdefault("permissible_values", CommentedMap())
        tpvs[loc.label] = pv
        if comment is not None:
            tpvs.ca.items[loc.label] = comment
        ledger_row(ctx, _tid(loc))["decision"] = rec["id"]


class TermMerge(Handler):
    kind, verdict = "term", "merge"

    def check(self, rec, ctx):
        loc = self.term(ctx, rec)
        into = self.term(ctx, rec, self.op(rec)["into"])
        if _tid(into) == _tid(loc):
            return ["a term cannot merge into itself"]
        if into.enum != loc.enum:
            return [f"cannot merge a {loc.enum} term into a {into.enum} term; "
                    "move_to_enum first"]
        return []

    def run(self, rec, ctx):
        loc = self.term(ctx, rec)
        into = self.term(ctx, rec, self.op(rec)["into"])
        tid, itid = _tid(loc), _tid(into)
        old_ref, new_ref = ctx.meaning(tid), ctx.meaning(itid)
        for _, _, pv in all_values(ctx):
            refs = [str(x) for x in (pv or {}).get("see_also") or []]
            if old_ref in refs:
                own = str(pv.get("meaning"))
                out = []
                for x in refs:
                    x = new_ref if x == old_ref else x
                    if x != own and x not in out:
                        out.append(x)
                set_list(pv, "see_also", out)
        relabel(ctx, loc, into.label)
        keep = self.op(rec)["mappings"] == "move"
        for path in sssom_paths(ctx):
            table = ctx.read_tsv(path)
            have = {(r["predicate_id"], r["object_id"], r.get("predicate_modifier", ""))
                    for r in table.rows if r["subject_id"] == new_ref}
            rows = []
            for r in table.rows:
                if r["subject_id"] == old_ref:
                    key = (r["predicate_id"], r["object_id"], r.get("predicate_modifier", ""))
                    if not keep or key in have:
                        continue
                    r["subject_id"], r["subject_label"] = new_ref, into.label
                    have.add(key)
                rows.append(r)
            table.rows[:] = rows
        del loc.pvs[loc.label]
        retire(ctx, tid, "merged", loc.enum, loc.label, rec["id"], replaced_by=itid)
        return Effects(retired=[rec["subject"]])


class TermDelete(Handler):
    kind, verdict = "term", "delete"

    def check(self, rec, ctx):
        refs = references(ctx, self.term(ctx, rec))
        return [f"{rec['subject']} is still referenced: " + "; ".join(refs)] if refs else []

    def run(self, rec, ctx):
        loc = self.term(ctx, rec)
        del loc.pvs[loc.label]
        retire(ctx, _tid(loc), "deleted", loc.enum, loc.label, rec["id"])
        return Effects(retired=[rec["subject"]])


class TermMapping(Handler):
    """adopt_mapping / reject_mapping on a term: a row in the SSSOM file for the
    object's prefix. Projects add rules (`wb.mapping_checks`) and columns
    (`wb.mapping_fill`)."""
    kind = "term"

    def __init__(self, verdict, negate):
        self.verdict, self.negate = verdict, negate

    def check(self, rec, ctx):
        op = self.op(rec)
        pred, obj = op["predicate"], op["object"]
        path = sssom_path_for(ctx, obj)
        errs = []
        if not (ctx.root / path).exists():
            errs.append(f"no mapping file {path} for object {obj}")
            return errs
        if not re.match(r"^[A-Za-z_]+:\S+$", pred):
            errs.append(f"predicate {pred!r} is not a CURIE")
        for fn in ctx.wb.mapping_checks:
            errs += fn(op, self.negate, ctx)
        return errs

    def run(self, rec, ctx):
        op = self.op(rec)
        loc = self.term(ctx, rec)
        pred, obj = op["predicate"], op["object"]
        table = ctx.read_tsv(sssom_path_for(ctx, obj))
        subj = ctx.meaning(_tid(loc))
        comment = op.get("comment") or " ".join(rec["rationale"].split())
        if self.negate:
            table.add_column("predicate_modifier", after="predicate_id")
        row = next((r for r in table.rows if r["subject_id"] == subj
                    and r["predicate_id"] == pred and r["object_id"] == obj), None)
        new = row is None
        if row is None:
            row = {c: "" for c in table.header}
            row.update(subject_id=subj, predicate_id=pred, object_id=obj)
            table.rows.append(row)
        row["subject_label"] = loc.label
        row["mapping_justification"] = MANUAL
        if op.get("object_label"):
            row["object_label"] = op["object_label"]
        row["comment"] = comment
        if "predicate_modifier" in table.header:
            row["predicate_modifier"] = "Not" if self.negate else ""
        for fn in ctx.wb.mapping_fill:
            fn(row, op, self.negate, table, ctx, new)


# ---------------------------------------------------------------- enum verdicts

class Described(Handler):
    """edit_description on an enum, subset or module-less element."""

    def __init__(self, kind, section):
        self.kind, self.verdict, self.section = kind, "edit_description", section

    def check(self, rec, ctx):
        _, body, _ = find(ctx, self.section, subject(ctx, rec["subject"])["name"])
        return [] if body is not None else [f"{rec['subject']} is not in {ctx.src}/"]

    def run(self, rec, ctx):
        _, body, _ = find(ctx, self.section, subject(ctx, rec["subject"])["name"])
        if "description" in body:
            body["description"] = self.op(rec)["description"]
        else:
            body.insert(0, "description", self.op(rec)["description"])


class EnumAddTerm(Handler):
    kind, verdict = "enum", "add_term"

    def check(self, rec, ctx):
        name = subject(ctx, rec["subject"])["name"]
        if find(ctx, "enums", name)[1] is None:
            return [f"{rec['subject']} is not in {ctx.src}/"]
        taken = label_taken(ctx, self.op(rec)["label"])
        return [taken] if taken else []

    def run(self, rec, ctx):
        op = self.op(rec)
        _, body, _ = find(ctx, "enums", subject(ctx, rec["subject"])["name"])
        ledger = ctx.read_tsv(ctx.ledger)
        local, digits = ctx.wb.cfg.term_local, ctx.wb.cfg.term_digits
        top = max((int(r["id"][len(local):]) for r in ledger.rows), default=0)
        tid = f"{local}{top + 1:0{digits}d}"
        ledger.rows.append({c: "" for c in ledger.header} | {"id": tid, "status": "live",
                                                             "decision": rec["id"]})
        pv = CommentedMap()
        if op.get("description"):
            pv["description"] = op["description"]
        pv["meaning"] = ctx.meaning(tid)
        if op.get("subsets"):
            pv["in_subset"] = CommentedSeq(subject(ctx, s)["name"] for s in op["subsets"])
        pvs = body.setdefault("permissible_values", CommentedMap())
        prev = pvs[list(pvs)[-1]] if pvs else None
        pvs[op["label"]] = pv
        # Carry the blank line that closed the enum over to the new last value.
        if isinstance(prev, CommentedMap) and prev and not isinstance(pv[list(pv)[-1]], list):
            tok = prev.ca.items.get(list(prev)[-1])
            if tok and len(tok) > 2 and tok[2] is not None:
                pv.ca.items[list(pv)[-1]] = [None, None, tok[2], None]
                tok[2] = None
        return Effects(created=[f"term:{tid}"])


class EnumRename(Handler):
    kind, verdict = "enum", "rename_enum"

    def check(self, rec, ctx):
        old, new = subject(ctx, rec["subject"])["name"], self.op(rec)["name"]
        errs = []
        why = protected(ctx, "enums").get(old)
        if why:
            errs.append(f"{old} is protected ({why}); change that before renaming")
        if find(ctx, "enums", new)[1] is not None or find(ctx, "classes", new)[1] is not None:
            errs.append(f"{new} is already defined")
        return errs

    def run(self, rec, ctx):
        old, new = subject(ctx, rec["subject"])["name"], self.op(rec)["name"]
        _, _, pool = find(ctx, "enums", old)
        for _, _, body in slots_ranging_on(ctx, old):
            if body.get("range") == old:
                body["range"] = new
            for key in ("any_of", "exactly_one_of", "all_of", "none_of"):
                for b in body.get(key) or []:
                    if (b or {}).get("range") == old:
                        b["range"] = new
        rename_key(pool, old, new)
        enum_renamed(ctx, old, new)
        new_sid = f"enum:{new}"
        return Effects(renamed={rec["subject"]: new_sid}, created=[new_sid])


class EnumMove(Handler):
    kind, verdict = "enum", "move_enum"

    def check(self, rec, ctx):
        mod, body, _ = find(ctx, "enums", subject(ctx, rec["subject"])["name"])
        target = subject(ctx, self.op(rec)["module"])["name"]
        if body is None:
            return [f"{rec['subject']} is not in {ctx.src}/"]
        errs = [f"{rec['subject']} is already in module {target}"] if mod == target else []
        name = subject(ctx, rec["subject"])["name"]
        why = protected(ctx, "enums").get(name)
        if why:
            errs.append(f"{name} is protected ({why}); change that before moving it")
        return errs

    def run(self, rec, ctx):
        name = subject(ctx, rec["subject"])["name"]
        mod, body, pool = find(ctx, "enums", name)
        comment = pool.ca.items.pop(name, None)
        pool.pop(name)
        if not pool:
            src(ctx, mod).pop("enums")
        tdata = src(ctx, subject(ctx, self.op(rec)["module"])["name"])
        tpool = tdata.setdefault("enums", CommentedMap())
        tpool[name] = body
        if comment is not None:
            tpool.ca.items[name] = comment


class EnumDelete(Handler):
    kind, verdict = "enum", "delete_enum"

    def check(self, rec, ctx):
        name = subject(ctx, rec["subject"])["name"]
        _, body, _ = find(ctx, "enums", name)
        if body is None:
            return [f"{rec['subject']} is not in {ctx.src}/"]
        errs = [f"slot {s} ranges on {name}" for _, s, _ in slots_ranging_on(ctx, name)]
        why = protected(ctx, "enums").get(name)
        if why:
            errs.append(f"{name} is protected ({why})")
        for label, pv in (body.get("permissible_values") or {}).items():
            loc = TermLoc(None, name, label, pv, body["permissible_values"])
            refs = [r for r in references(ctx, loc) if not r.startswith("example value")]
            if refs:
                errs.append(f"{name} {label!r} is still referenced: " + "; ".join(refs))
        return errs

    def run(self, rec, ctx):
        name = subject(ctx, rec["subject"])["name"]
        mod, body, pool = find(ctx, "enums", name)
        retired = [rec["subject"]]
        for label, pv in (body.get("permissible_values") or {}).items():
            tid = str(pv["meaning"]).split(":", 1)[1]
            retire(ctx, tid, "deleted", name, label, rec["id"])
            retired.append(f"term:{tid}")
        pool.pop(name)
        if not pool:
            src(ctx, mod).pop("enums")
        return Effects(retired=retired)


# ---------------------------------------------------------------- subset verdicts

def _members(ctx, name):
    return [pv for _, _, pv in all_values(ctx) if name in ((pv or {}).get("in_subset") or [])]


class SubsetRename(Handler):
    kind, verdict = "subset", "rename_subset"

    def check(self, rec, ctx):
        new = self.op(rec)["name"]
        return [f"subset {new} already exists"] if find(ctx, "subsets", new)[1] is not None else []

    def run(self, rec, ctx):
        old, new = subject(ctx, rec["subject"])["name"], self.op(rec)["name"]
        _, _, pool = find(ctx, "subsets", old)
        for pv in _members(ctx, old):
            seq = pv["in_subset"]
            seq[seq.index(old)] = new
        rename_key(pool, old, new)
        body = pool[new]
        if isinstance(body, dict) and body.get("name") == old:
            body["name"] = new
        new_sid = f"subset:{new}"
        return Effects(renamed={rec["subject"]: new_sid}, created=[new_sid])


class SubsetMerge(Handler):
    kind, verdict = "subset", "merge_subset"

    def check(self, rec, ctx):
        return ["a subset cannot merge into itself"] \
            if self.op(rec)["into"] == rec["subject"] else []

    def run(self, rec, ctx):
        old = subject(ctx, rec["subject"])["name"]
        into = subject(ctx, self.op(rec)["into"])["name"]
        for pv in _members(ctx, old):
            names = [into if s == old else s for s in pv["in_subset"]]
            set_list(pv, "in_subset", list(dict.fromkeys(names)))
        _, _, pool = find(ctx, "subsets", old)
        pool.pop(old)
        return Effects(retired=[rec["subject"]])


class SubsetDelete(Handler):
    kind, verdict = "subset", "delete_subset"

    def check(self, rec, ctx):
        name = subject(ctx, rec["subject"])["name"]
        n = len(_members(ctx, name))
        return [f"subset {name} still has {n} member(s); merge_subset or set_subsets first"] if n else []

    def run(self, rec, ctx):
        name = subject(ctx, rec["subject"])["name"]
        _, _, pool = find(ctx, "subsets", name)
        pool.pop(name)
        return Effects(retired=[rec["subject"]])


# ---------------------------------------------------------------- registration

def handlers():
    """Every generic content handler instance."""
    return [
        TermEditDescription(),
        TermSetSubsets(),
        TermSeeAlso("add_see_also", add=True),
        TermSeeAlso("remove_see_also", add=False),
        TermRename(),
        TermMoveToEnum(),
        TermMerge(),
        TermDelete(),
        TermMapping("adopt_mapping", negate=False),
        TermMapping("reject_mapping", negate=True),
        Described("enum", "enums"),
        EnumAddTerm(),
        EnumRename(),
        EnumMove(),
        EnumDelete(),
        Described("subset", "subsets"),
        SubsetRename(),
        SubsetMerge(),
        SubsetDelete(),
    ]


REF_PROVIDERS = [SeeAlsoRefs(), SssomRefs(), ExampleRefs()]
