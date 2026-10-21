"""Schema linter: machine findings over every subject kind.

Reads subjects.json, the schema files (for attached comments), decisions.yaml
and policy.yaml (`lint:` thresholds, and per rule a severity and the suggested
verdict, which must be one verdicts.yaml allows for a kind the finding names).
Writes `<out>/findings/schema_lint.json`:

  {_generated_by, schema_fingerprint, counts: {rule: n},
   findings: [{id, rule, severity, message, subjects: [sid...], suggested_verdict}]}

A finding's id is `lint:<rule>:` + sha1 of its sorted subject SIDs (first 10
hex), so it is stable across runs. A rule missing from policy.yaml `lint.rules`
is switched off. One finding per (rule, subject set);
several hits on one subject are joined into its message.

Generic rules live here. Projects add rules with `wb.lint_rules.append(fn)`,
where fn(linter) calls `linter.emit(...)`. Options (workbench.yaml `options:`):
  content_enums           enums whose terms get the mapping and usage rules
                          (default: every enum)
  usage_label             noun for term usage counts in messages (default "uses")
  import_source_prefix    `source:` prefix marking imported classes
  doc_link_prefix_suffix  a CURIE prefix ending in this is a documentation page
"""
import hashlib
import json
import re
from collections import defaultdict

import yaml

from .signals import term_usage
from .store import load_verdicts

MAPPING_KEYS = ("exact_mappings", "close_mappings", "broad_mappings",
                "narrow_mappings", "related_mappings", "mappings")
SECTIONS = ("classes", "slots", "enums", "subsets")
GENERATED_BY = "cwb lint; do not edit"


class LintError(Exception):
    pass


def _desc(s):
    return (s["payload"].get("description") or "").strip()


def _norm(text):
    return re.sub(r"\s+", " ", text).strip().lower()


def word_in(word, label):
    return re.search(r"(?<![\w-])" + re.escape(word.lower()) + r"(?![\w-])", label.lower()) is not None


class Linter:
    def __init__(self, wb):
        self.wb = wb
        cfg = wb.cfg
        data = json.loads(cfg.output("subjects.json").read_text())
        self.fingerprint = data["schema_fingerprint"]
        self.all = list(data["subjects"].values())
        self.subjects = [s for s in self.all if not s.get("retired")]
        self.by_sid = {s["sid"]: s for s in self.subjects}
        self.of_kind = defaultdict(list)
        for s in self.subjects:
            self.of_kind[s["kind"]].append(s)
        self.term_by_label = {(s["enum"], s["label"]): s["sid"] for s in self.of_kind["term"]}
        self.module_layer = {s["name"]: s["layer"] for s in self.of_kind["module"]}
        self.class_layer = {s["name"]: s["layer"] for s in self.of_kind["class"]}
        self.enums = {s["name"] for s in self.of_kind["enum"]}
        self.pol = yaml.safe_load(cfg.policy.read_text())["lint"]
        self.markers = [re.compile(p, re.I) for p in self.pol.get("todo_markers") or []]
        self.usage = term_usage(wb)
        self.verdicts = load_verdicts(cfg.verdicts)
        self.content_enums = wb.option("content_enums")
        self.findings = {}

    # -- helpers -----------------------------------------------------------
    def emit(self, rule, sids, message):
        if rule not in self.pol["rules"]:
            return  # a rule the project's policy does not configure is off
        sids = sorted(set(sids))
        missing = [s for s in sids if s not in self.by_sid]
        if missing:
            raise LintError(f"rule {rule} names unknown subjects {missing}")
        key = hashlib.sha1("\n".join(sids).encode()).hexdigest()[:10]
        fid = f"lint:{rule}:{key}"
        if fid in self.findings:
            self.findings[fid]["message"] += f"; {message}"
            return
        rcfg = self.pol["rules"][rule]
        kinds = {self.by_sid[s]["kind"] for s in sids}
        if not any(rcfg["suggested_verdict"] in self.verdicts.get(k, {}) for k in kinds):
            raise LintError(f"policy suggests {rcfg['suggested_verdict']!r} for rule {rule}, "
                            f"which verdicts.yaml does not allow for {sorted(kinds)}")
        self.findings[fid] = {
            "id": fid, "rule": rule, "severity": rcfg["severity"], "message": message,
            "subjects": sids, "suggested_verdict": rcfg["suggested_verdict"],
        }

    def positive_rows(self, term):
        return [r for r in term["payload"].get("sssom") or []
                if (r.get("modifier") or "") != "Not"]

    def is_content(self, s):
        return s["kind"] == "term" and (self.content_enums is None or s["enum"] in self.content_enums)

    # -- slot rules --------------------------------------------------------
    def slot_rules(self):
        for s in self.of_kind["slot"]:
            p, sid, name = s["payload"], s["sid"], s["name"]
            if p.get("required") and not p.get("identifier"):
                self.emit("required-policy", [sid],
                          f"slot {name} is required but is not an identifier; "
                          "every required slot is a cost to adopters")
            if not s.get("used_by") and not s.get("inherited_by"):
                self.emit("unused-slot", [sid], f"slot {name} is listed by no class")
            if len(s.get("used_by") or []) > 1:
                self.emit("shared-slot", [sid],
                          f"slot {name} is listed by {', '.join(s['used_by'])}; "
                          "confirm the meaning is shared")
            if not p.get("examples"):
                self.emit("slot-no-examples", [sid], f"slot {name} has no examples")
            rng = p.get("range")
            if rng in self.class_layer and self.wb.cfg.layer_annotation:
                own = self.module_layer[s["module"]]
                if self.class_layer[rng] > own:
                    self.emit("layer-violation", [sid, f"class:{rng}"],
                              f"slot {name} (layer {own}) ranges on {rng} "
                              f"(layer {self.class_layer[rng]})")

    # -- mapping rules -----------------------------------------------------
    def doc_link_rule(self):
        suffix = self.wb.option("doc_link_prefix_suffix")
        for s in self.of_kind["class"] + self.of_kind["slot"]:
            for key in MAPPING_KEYS:
                for v in s["payload"].get(key) or []:
                    prefix = v.split(":", 1)[0] if ":" in v else ""
                    if (suffix and prefix.endswith(suffix)) or v.endswith(".html"):
                        self.emit("mapping-target-is-doc-link", [s["sid"]],
                                  f"{s['kind']} {s['name']} {key} value {v} is a "
                                  "documentation page, not a term")

    def term_rules(self):
        high = self.pol.get("high_usage_files")
        noun = self.wb.option("usage_label", "uses")
        for t in self.of_kind["term"]:
            sid, label = t["sid"], t["label"]
            if not _desc(t):
                self.emit("enum-value-no-description", [sid],
                          f"{t['enum']} value '{label}' has no description")
            for r in t["payload"].get("sssom") or []:
                if r.get("justification") == "semapv:UnspecifiedMatching":
                    self.emit("unreviewed-mapping", [sid],
                              f"'{label}' {r['predicate']} {r['object']} "
                              "has no mapping justification")
            if not self.is_content(t):
                continue
            positive = self.positive_rows(t)
            if not positive:
                self.emit("no-mapping-at-all", [sid], f"'{label}' has no SSSOM mapping")
            if not self.usage:
                continue
            files = self.usage.get(sid, 0)
            if files == 0:
                self.emit("zero-usage-term", [sid], f"'{label}' has no {noun}")
            elif high is not None and files >= high and not positive:
                self.emit("unmapped-high-usage", [sid],
                          f"'{label}' has {files} {noun} and no SSSOM mapping")

    def slot_example_values(self):
        vals = set()
        for s in self.of_kind["slot"]:
            for ex in s["payload"].get("examples") or []:
                if isinstance(ex, dict) and ex.get("value") is not None:
                    vals.add(str(ex["value"]))
        return vals

    # -- enum, class, subset, module rules -----------------------------------
    def enum_rules(self):
        for e in self.of_kind["enum"]:
            if not e.get("referenced_by"):
                self.emit("unused-enum", [e["sid"]], f"enum {e['name']} is the range of no slot")
            mod = next(m for m in self.of_kind["module"] if m["name"] == e["module"])
            if any(c["module"] == mod["name"] for c in self.of_kind["class"]):
                self.emit("inline-enum", [e["sid"]],
                          f"enum {e['name']} is defined inside class module {mod['name']}")

    def class_rules(self):
        prefix = self.wb.option("import_source_prefix")
        if not prefix:
            return
        for c in self.of_kind["class"]:
            p = c["payload"]
            if (str(p.get("source") or "").startswith(prefix)
                    and not p.get("conforms_to")
                    and not any(p.get(k) for k in MAPPING_KEYS)):
                self.emit("class-no-alignment", [c["sid"]],
                          f"ported class {c['name']} has no conforms_to and no mappings")

    def subset_rules(self):
        big = self.pol.get("oversized_subset")
        for s in self.of_kind["subset"]:
            n = len(s.get("members") or [])
            if n == 1:
                self.emit("singleton-subset", [s["sid"]], f"subset {s['name']} has one member")
            elif big is not None and n > big:
                self.emit("oversized-subset", [s["sid"]],
                          f"subset {s['name']} has {n} members (> {big})")

    # -- description rules -------------------------------------------------
    def description_rules(self):
        terse = self.pol.get("terse_description_chars", 0)
        seen = defaultdict(list)
        for s in self.of_kind["term"] + self.of_kind["slot"] + self.of_kind["class"]:
            d = _desc(s)
            if d and len(d) < terse:
                self.emit("terse-description", [s["sid"]],
                          f"{s['kind']} {s['name']} description is {len(d)} chars "
                          f"(< {terse})")
            if d:
                seen[_norm(d)].append(s["sid"])
        for d, sids in seen.items():
            if len(sids) > 1:
                self.emit("duplicate-description", sids,
                          f"{len(sids)} subjects share the description '{d[:60]}'")
        for s in self.of_kind["module"] + self.of_kind["enum"]:
            sentences = [x for x in re.split(r"(?<=[.!?])\s+", _norm(_desc(s))) if len(x) >= 40]
            dup = sorted({x for x in sentences if sentences.count(x) > 1})
            if dup:
                self.emit("duplicate-paragraph", [s["sid"]],
                          f"{s['kind']} {s['name']} description repeats '{dup[0][:60]}'")

    def todo_rules(self):
        for s in self.subjects:
            d = _desc(s)
            if any(m.search(d) for m in self.markers):
                self.emit("todo-marker", [s["sid"]],
                          f"{s['kind']} {s['name']} description carries a TODO/placeholder marker")
        cfg = self.wb.cfg
        for path in sorted(cfg.schema_dir.glob("*.yaml")):
            if path.name in cfg.schema_exclude:
                continue
            for lineno, comment, ctx in comments(path):
                if any(m.search(comment) for m in self.markers):
                    sid = self.context_sid(path.stem, ctx)
                    self.emit("todo-marker", [sid],
                              f"{path.name}:{lineno} comment '{comment.strip()[:80]}'")

    def context_sid(self, module, path):
        """Map a YAML key path to the innermost subject; fall back to the module."""
        cands = []
        if len(path) >= 2 and path[0] in SECTIONS:
            kind = {"classes": "class", "slots": "slot", "enums": "enum", "subsets": "subset"}[path[0]]
            cands.append(f"{kind}:{path[1]}")
            if path[0] == "enums" and len(path) >= 4 and path[2] == "permissible_values":
                cands.insert(0, self.term_by_label.get((path[1], path[3])))
            if path[0] == "classes" and len(path) >= 4 and path[2] == "slot_usage":
                cands.insert(0, f"usage:{path[1]}/{path[3]}")
        cands.append(f"module:{module}")
        return next(c for c in cands if c in self.by_sid)

    def run(self):
        self.slot_rules()
        self.doc_link_rule()
        self.term_rules()
        for rule in self.wb.lint_rules:
            rule(self)
        self.enum_rules()
        self.class_rules()
        self.subset_rules()
        self.description_rules()
        self.todo_rules()
        findings = sorted(self.findings.values(), key=lambda f: (f["rule"], f["id"]))
        counts = defaultdict(int)
        for f in findings:
            counts[f["rule"]] += 1
        return {"_generated_by": GENERATED_BY, "schema_fingerprint": self.fingerprint,
                "counts": {r: counts.get(r, 0) for r in sorted(self.pol["rules"])},
                "findings": findings}


_KEY = re.compile(r"""^( *)(?:- +)?(['"]?)([^'"#:][^#]*?)\2:(?:\s|$)""")


def comments(path):
    """Yield (lineno, comment text, key path of the element it is attached to).

    A comment attaches to the element of the line just above it; a comment
    preceded by a blank line (a free-standing block) attaches to the next
    element below it. Full-line and trailing comments are both scanned.
    """
    lines = path.read_text().splitlines()
    ctx, stack = [None] * len(lines), []
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        m = _KEY.match(line)
        if m and not stripped.startswith("-"):
            stack.append((indent, m.group(3).strip()))
            ctx[i] = [k for _, k in stack]
        else:
            ctx[i] = [k for _, k in stack]
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            comment = stripped
        elif "#" in line and ctx[i] is not None and re.search(r"\s#\s", line):
            yield i + 1, line[line.index(" #") + 2:], ctx[i]
            continue
        else:
            continue
        above = next((j for j in range(i - 1, -1, -1)
                      if lines[j].strip() and not lines[j].lstrip().startswith("#")), None)
        blank_before = i > 0 and not lines[i - 1].strip()
        if above is not None and not blank_before:
            yield i + 1, comment, ctx[above]
            continue
        below = next((ctx[j] for j in range(i + 1, len(lines))
                      if ctx[j] and len(ctx[j]) >= 2), [])
        yield i + 1, comment, below


def render(wb):
    data = Linter(wb).run()
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    summary = " ".join(f"{r}={n}" for r, n in data["counts"].items() if n) \
        + f" total={len(data['findings'])}"
    return text, summary
