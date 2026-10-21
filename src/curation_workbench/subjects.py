"""The subject registry: every reviewable thing in the schema, with a permanent id.

One subject per term, enum, class, slot, slot-usage override, subset and module.
A subject id (SID) is `<kind>:<key>` and is opaque: no code may split one to
recover a part, so every record also carries `kind`, `name` and its container.

    term:<term id>                 term:MS_0000123
    enum:<EnumName>                enum:FileKind
    class:<ClassName>              class:File
    slot:<slot_name>               slot:file_id
    usage:<ClassName>/<slot_name>  usage:File/file_label
    subset:<subset_name>           subset:signal_track
    module:<module_name>           module:file

A slot's SID is its global name (LinkML slot names are one namespace).
`usage:` exists only where a `slot_usage:` override is authored.

`hash` covers the authored source of truth only (`payload`), never a
projection: a term's payload holds its SSSOM rows, not the `*_mappings` slots
projected from them.

Writes `subjects.json`, `term_crosswalk.json` and `subject_aliases.json`.
"""
import hashlib
import json
import re

from . import schema, store
from .termids import TermIds

KINDS = ("module", "subset", "enum", "term", "class", "slot", "usage")
TERM_KEYS = ("description", "in_subset", "aliases", "keywords", "status")
MODULE_KEYS = ("id", "name", "title", "description", "imports")


class RegistryError(Exception):
    pass


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def slug(kind, key):
    return kind + "-" + re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")


def module_layer(wb, mod_name, data):
    ann = wb.cfg.layer_annotation
    if not ann:
        return None
    layer = (data.get("annotations") or {}).get(ann)
    if not isinstance(layer, int):
        raise RegistryError(f"{schema.module_rel(wb.cfg, mod_name)} has no integer annotations.{ann}")
    return layer


def build(wb):
    """(records {sid: record}, crosswalk {"labels": ..., "legacy": ...})."""
    cfg = wb.cfg
    tids = TermIds(cfg)
    modules = schema.load_modules(cfg)
    layers = {m: module_layer(wb, m, d) for m, d in modules.items()}
    classes, slots, enums, subsets = {}, {}, {}, {}
    for mod, d in modules.items():
        for kind, pool in (("classes", classes), ("slots", slots),
                           ("enums", enums), ("subsets", subsets)):
            for name, body in (d.get(kind) or {}).items():
                if name in pool:
                    raise RegistryError(f"{kind[:-1]} {name!r} is defined in both "
                                        f"{pool[name][0]} and {mod}")
                pool[name] = (mod, body or {})

    # SSSOM rows by term id.
    cols = cfg.sssom_columns
    sssom = {}
    for fname, r in schema.all_sssom_rows(cfg):
        sid = r.get("subject_id", "")
        tid = tids.from_meaning(sid)
        if tid is None:
            raise RegistryError(f"{fname}: subject_id {sid!r} is not a term id")
        sssom.setdefault(tid, []).append({k: (r.get(col) or "") for k, col in cols.items()})

    records = {}

    def add(kind, key, name, mod, payload, **extra):
        sid = f"{kind}:{key}"
        if sid in records:
            raise RegistryError(f"duplicate subject {sid}")
        rec = {"sid": sid, "kind": kind, "name": name,
               "module": mod, "layer": layers.get(mod) if mod else None,
               "file": schema.module_rel(cfg, mod) if mod else None,
               "slug": slug(kind, key), "retired": False,
               "payload": payload, "hash": sha256(canonical(payload))}
        rec.update(extra)
        records[sid] = rec
        return rec

    def parents(cname):
        body = classes.get(cname, (None, {}))[1]
        return ([body["is_a"]] if body.get("is_a") else []) + list(body.get("mixins") or [])

    def ancestors(cname, seen=None):
        seen = set() if seen is None else seen
        for p in parents(cname):
            if p not in seen:
                seen.add(p)
                ancestors(p, seen)
        return seen

    anc = {c: ancestors(c) for c in classes}
    descendants = {c: sorted(d for d in classes if c in anc[d]) for c in classes}

    # Modules.
    for mod, d in modules.items():
        payload = {k: d[k] for k in MODULE_KEYS if k in d}
        if cfg.layer_annotation:
            payload[cfg.layer_annotation] = layers[mod]
        members = sorted(
            [f"class:{c}" for c, (m, _) in classes.items() if m == mod]
            + [f"slot:{s}" for s, (m, _) in slots.items() if m == mod]
            + [f"enum:{e}" for e, (m, _) in enums.items() if m == mod]
            + [f"subset:{s}" for s, (m, _) in subsets.items() if m == mod])
        add("module", mod, mod, mod, payload, container=None, members=members)

    # Enums and terms.
    subset_members = {s: [] for s in subsets}
    labels, legacy = {}, {}
    for ename, (mod, body) in enums.items():
        member_ids = []
        for label, pv in (body.get("permissible_values") or {}).items():
            pv = pv or {}
            tid = tids.from_meaning(pv.get("meaning"))
            if tid is None:
                raise RegistryError(f"{ename} {label!r} has no {cfg.term_prefix}:{cfg.term_local}"
                                    f"{'N' * cfg.term_digits} meaning")
            member_ids.append(tid)
            see_also = []
            for ref in pv.get("see_also") or []:
                ref = str(ref)
                see_also.append(tids.from_meaning(ref) or ref)
            payload = {"label": label, "enum": ename, "see_also": see_also,
                       "sssom": sorted(sssom.get(tid, []), key=canonical)}
            payload.update({k: pv[k] for k in TERM_KEYS if k in pv})
            for s in pv.get("in_subset") or []:
                if s not in subset_members:
                    raise RegistryError(f"{ename} {label!r} is in undefined subset {s!r}")
                subset_members[s].append(tid)
            add("term", tid, tid, mod, payload, label=label, term_id=tid,
                enum=ename, container=f"enum:{ename}")
            _crosswalk(wb, labels, legacy, tid, ename, label)
        payload = {k: v for k, v in body.items() if k != "permissible_values"}
        payload["members"] = member_ids
        add("enum", ename, ename, mod, payload, container=f"module:{mod}",
            members=[f"term:{t}" for t in member_ids],
            referenced_by=sorted(f"slot:{s}" for s, (_, b) in slots.items()
                                 if ename in schema.slot_ranges(b)))

    # Retired terms (ledger only).
    ledger = tids.read_ledger()
    live = {r["name"] for r in records.values() if r["kind"] == "term"}
    for row in ledger:
        tid = row["id"]
        if row["status"] == "live":
            if tid not in live:
                raise RegistryError(f"ledger says {tid} is live, but no value has it")
            continue
        payload = {"label": row.get("label", ""), "enum": row.get("enum", ""),
                   "status": row["status"], "replaced_by": row.get("replaced_by", ""),
                   "decision": row.get("decision", "")}
        rec = add("term", tid, tid, None, payload, label=row.get("label", ""),
                  term_id=tid, enum=row.get("enum", ""), container=None)
        rec["retired"] = True
        if row.get("label"):
            _crosswalk(wb, labels, legacy, tid, row.get("enum", ""), row["label"])
    for row in ledger:
        for former in filter(None, (row.get("former_labels") or "").split("|")):
            _crosswalk(wb, labels, legacy, row["id"], row.get("enum", ""), former)

    # Subsets.
    for sname, (mod, body) in subsets.items():
        members = sorted(subset_members[sname])
        payload = {"description": body.get("description", ""), "members": members}
        add("subset", sname, sname, mod, payload, container=f"module:{mod}",
            members=[f"term:{t}" for t in members])

    # Classes, slots, usages.
    listers = {s: [] for s in slots}
    for cname, (mod, body) in classes.items():
        for s in body.get("slots") or []:
            if s not in listers:
                raise RegistryError(f"class {cname} lists undefined slot {s!r}")
            listers[s].append(cname)
    for cname, (mod, body) in classes.items():
        add("class", cname, cname, mod, body, container=f"module:{mod}",
            inherited_by=descendants[cname],
            referenced_by=sorted(f"slot:{s}" for s, (_, b) in slots.items()
                                 if cname in schema.slot_ranges(b)))
        for sname, usage in (body.get("slot_usage") or {}).items():
            add("usage", f"{cname}/{sname}", sname, mod, usage or {},
                container=f"class:{cname}", owner_class=cname)
    for sname, (mod, body) in slots.items():
        used_by = sorted(listers[sname])
        inherited = sorted(c for c in classes if c not in used_by
                           and any(a in used_by for a in anc[c]))
        add("slot", sname, sname, mod, body, container=f"module:{mod}",
            defined_in=mod, owner_class=used_by[0] if len(used_by) == 1 else None,
            used_by=used_by, inherited_by=inherited)

    slugs = {}
    for sid, rec in records.items():
        if rec["slug"] in slugs:
            raise RegistryError(f"slug {rec['slug']!r} is shared by {slugs[rec['slug']]} and {sid}")
        slugs[rec["slug"]] = sid

    order = {k: i for i, k in enumerate(KINDS)}
    records = dict(sorted(records.items(), key=lambda kv: (order[kv[1]["kind"]], kv[0])))
    return records, {"labels": dict(sorted(labels.items())),
                     "legacy": dict(sorted(legacy.items()))}


def _crosswalk(wb, labels, legacy, tid, enum, label):
    """Record every reference form a label ever had, pointing at its term id.
    Plugins add legacy forms (old CURIEs, old IRIs) via `wb.legacy_forms`."""
    if labels.get(label, tid) != tid:
        raise RegistryError(f"label {label!r} maps to both {labels[label]} and {tid}")
    labels[label] = tid
    for fn in wb.legacy_forms:
        for form in fn(tid, enum, label):
            if legacy.get(form, tid) != tid:
                raise RegistryError(f"legacy form {form!r} maps to both {legacy[form]} and {tid}")
            legacy[form] = tid


def counts(records):
    out = {k: 0 for k in KINDS}
    retired = 0
    for rec in records.values():
        if rec["retired"]:
            retired += 1
        else:
            out[rec["kind"]] += 1
    out = {k: out[k] for k in ("term", "enum", "class", "slot", "usage", "subset", "module")}
    out["total"] = sum(out.values())
    if retired:
        out["retired_term"] = retired
    return out


def live_counts(wb):
    """The same counts, taken straight from the YAML (the assertion's other side)."""
    modules = schema.load_modules(wb.cfg)
    n = {"term": 0, "enum": 0, "class": 0, "slot": 0, "usage": 0, "subset": 0,
         "module": len(modules)}
    for d in modules.values():
        n["enum"] += len(d.get("enums") or {})
        n["term"] += sum(len((e or {}).get("permissible_values") or {})
                         for e in (d.get("enums") or {}).values())
        n["class"] += len(d.get("classes") or {})
        n["usage"] += sum(len((c or {}).get("slot_usage") or {})
                          for c in (d.get("classes") or {}).values())
        n["slot"] += len(d.get("slots") or {})
        n["subset"] += len(d.get("subsets") or {})
    n["total"] = sum(n.values())
    return n


def fingerprint(records):
    lines = sorted(f"{sid}:{rec['hash']}" for sid, rec in records.items())
    return sha256("\n".join(lines))


def aliases(wb):
    """{old SID: new SID} from applied rename records, in id order."""
    out = {}
    for d in store.plain_records(wb.cfg.decisions):
        if d.get("status") == "applied" and str(d.get("verdict", "")).startswith("rename"):
            out.update((d.get("applied") or {}).get("renamed") or {})
    return dict(sorted(out.items()))


def dump_json(d):
    return json.dumps(d, indent=1, ensure_ascii=False, default=str) + "\n"


GENERATED_BY = "cwb subjects; do not edit"


def render(wb):
    """{output path: text} for the three registry files, plus the counts."""
    records, crosswalk = build(wb)
    c = counts(records)
    live = live_counts(wb)
    got = {k: c[k] for k in live}
    if got != live:
        raise RegistryError(f"counts {got} disagree with the schema {live}")
    doc = {"_generated_by": GENERATED_BY, "schema_fingerprint": fingerprint(records),
           "counts": c, "subjects": records}
    out = wb.cfg.output
    return {out("subjects.json"): dump_json(doc),
            out("term_crosswalk.json"): dump_json({"_generated_by": GENERATED_BY, **crosswalk}),
            out("subject_aliases.json"): dump_json(aliases(wb))}, c
