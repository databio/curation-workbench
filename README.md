# curation-workbench

A decision-driven curation workbench for [LinkML](https://linkml.io/) schemas
and [SSSOM](https://mapping-commons.github.io/sssom/) mappings.

Every reviewable piece of a schema (term, enum, class, slot, slot usage,
subset, module) gets a permanent subject id and a content hash. Curators record
**decisions** about subjects (keep, rename, merge, adopt a mapping, set a
field's cardinality, ...) in a plain YAML log. An **apply** step turns pending
decisions into edits of the schema and mapping files, all or nothing, and
stamps each decision as applied. Automatic **lint** findings and a ranked
**queue** say what to look at next, and **review types** split that queue into
kinds of work with one question each.

In local mode everything is plain files in git and there is no database. A
hosted, multi-user mode (in progress) keeps votes and hosted decisions in
Postgres; see [Storage](#storage).

It was extracted from ONGA (Ontology of Genomic Annotations), which is its
first project; see `adapters/onga/`.

## Install

```bash
uv venv && uv pip install -e ".[test]"          # tests include the Postgres store's
uv pip install -e ".[postgres]"                 # hosted store only (SQLAlchemy + psycopg 3)
uv pip install -e ".[hosted]"                   # hosted mode (store + sync worker)
```

## Commands

```bash
cwb -c workbench.yaml subjects [--check]   # subjects.json, term_crosswalk.json, subject_aliases.json
cwb -c workbench.yaml lint [--check]       # findings/schema_lint.json
cwb -c workbench.yaml status [--check]     # status.json: state + queue score per subject
cwb -c workbench.yaml review [--check]     # review.json: queue sorted into review tracks/types
cwb -c workbench.yaml regen [--check]      # all of the above, in order
cwb -c workbench.yaml snapshot [--out F]   # the commit-phase bundle as JSON (see below)
cwb derive --snapshot F --decisions D [--out DIR]  # status.json + review.json from a bundle
cwb -c workbench.yaml apply [--dry-run] [--ids DEC-0001 ...] [--emit pending.json]
cwb -c workbench.yaml serve [--port 8781] [--ui DIR]  # local daemon + review UI (loopback only)
```

Global flags: `--root` (the project tree; overrides the config) and `--out`
(where generated files go; default the curation dir). `--decisions` takes a
JSON list of decision records or a `decisions.yaml`.

Regen runs in two phases. `snapshot` computes everything that depends only on
the repo at one commit (subjects, aliases, lint and plugin findings, every
queue signal) into one JSON bundle; it is the only place plugin code runs.
`derive` turns a bundle plus a decision list into status.json and review.json
with no plugin calls. `cwb regen` is the two in a row, and the daemon keeps
one bundle in memory and re-derives status on each request.

Hosted mode adds (needs the `hosted` extra; see `dev-docs/sync.md`):

```bash
cwb runner [--port 8150]                     # the isolated runner: all repo code runs here
cwb hosted worker                            # the sync worker (mirrors repos, publishes snapshots)
cwb hosted resident add --project onga --repo https://github.com/databio/onga \
    --branch master --config workbench.yaml --plugin-dir curation/plugin
cwb hosted resident list | show | check | plugin-diff | approve-plugin --tree T |
    revoke-plugin | sync [--wait] | update | disable | rotate-webhook-secret <slug>
cwb hosted isolation-check                   # in the worker task: prove the runner is isolated
```

## A project

```
my-schema/
  workbench.yaml
  src/*.yaml                  LinkML modules (root module's imports = mint order)
  mappings/*.sssom.tsv        SSSOM mappings, one file per object prefix
  curation/
    decisions.yaml            the decision log (version, next_id, decisions: [])
    verdicts.yaml             which decisions exist, per subject kind, with field schemas
    policy.yaml               lint thresholds/rules, queue weights, severities
    term_ids.tsv              permanent term-id ledger
    review_types.yaml         optional: review tracks and types
```

`tests/fixtures/toy/` is a complete minimal project with no plugins.

### workbench.yaml

```yaml
project: myschema
root: .
schema: {dir: src, root: myschema.yaml, exclude: [], layer_annotation: null}
terms:                      # permissible values carry `meaning: ms:MS_0000001`
  prefix: ms
  local: MS_
  digits: 7
  base: https://example.org/ms/
  ledger: curation/term_ids.tsv
mappings:
  sssom: "mappings/*.sssom.tsv"
  columns: {predicate: predicate_id, object: object_id, modifier: predicate_modifier,
            justification: mapping_justification, comment: comment}
curation: {dir: curation}
review_types: null          # path relative to this file; default curation/review_types.yaml
plugins: []                 # modules importable from this file's folder
plugin_dir: null            # folder (relative to this file) holding the plugin modules
                            # and their helpers; when set, plugins import from it only
options:                    # read by generic rules and signals
  adoption_root: class:File          # queue signal: reachable from this class
  import_source_prefix: null         # classes whose `source:` starts with this are "ported"
  machine_definitions: null          # YAML of machine-drafted descriptions
  content_enums: null                # enums whose terms get mapping/usage lint rules
  usage_label: uses                  # noun for term usage counts in messages
  doc_link_prefix_suffix: null       # CURIE prefixes that point at web pages
  examples_dir: examples             # instance examples rewritten on rename
  apply_rank: {}                     # extra verdict ordering for apply
  layer_range: [1, 4]
  allowed_origins: [...]             # daemon CORS; default localhost:4321-4329
apply:                      # optional; defaults: snapshot schema/curation/mappings,
  snapshot: [...]           # no extra commands, regen in-process
  after_write: ["{python} scripts/project_specific_step.py"]
  regen: ["make regen"]     # replaces the in-process regen
  verify: ["make test"]
```

## Plugin API

A plugin is a module with `register(wb)`. Generic code never names a project;
everything project-specific goes through these hooks:

| Hook | Signature | Used by |
|---|---|---|
| `wb.legacy_forms` | `(tid, enum, label) -> [str]` | term crosswalk |
| `wb.findings_sources` | `(wb, fingerprint, severity_of) -> (findings, sources)` | status, review |
| `wb.signals[name]` | `(ctx) -> {sid: value}` (one per `queue:` weight) | status |
| `wb.masked_signals[name]` | same; zeroed where a terminal decision exists (builtin: `port`) | status |
| `wb.term_usage` | `(wb) -> {term sid: count}` | `impact` signal, usage lint rules |
| `wb.lint_rules` | `(linter) -> None`, calls `linter.emit(rule, sids, msg)` | lint |
| `wb.operation_checks` | `(verdict, op) -> [error]` | decision validation |
| `wb.record_checks` | `(wb, rec) -> [error]` | decision validation |
| `wb.handler(obj)` | handler with `kind`, `verdict`, `preflight`, `apply` | apply |
| `wb.handler_check(kind, verdict, fn)` | `(handler, rec, ctx) -> [error]` | apply preflight |
| `wb.ref_providers` | objects with `find` / `relabel` / `enum_renamed` | term rename/move/merge/delete |
| `wb.mapping_checks` | `(op, negate, ctx) -> [error]` | SSSOM adopt/reject |
| `wb.mapping_fill` | `(row, op, negate, table, ctx, new)` | SSSOM adopt/reject |
| `wb.protected_names` | `(ctx, section) -> {name: reason}` | class/enum rename, move, delete |
| `wb.routes` | `(method, prefix, fn(api, tail))` | daemon |
| `wb.regen` | `(wb, out_dir)` | `cwb regen`, in-process apply regen |
| `wb.suggestions` | `(wb) -> {finding id: {object, label, source, score, definition}}` | review UI: `links` answers (bundle `suggestions`) |
| `wb.browse_url` | `(wb, subject) -> url \| None` (one function, not a list) | review UI: "view in schema site" (bundle `browse`) |
| `wb.subject_panels` | `(wb) -> {sid: [{title, text?, links?: [{label, href?, sid?}], table?: {columns, rows}}]}` | review UI: subject page (bundle `panels`) |
| `wb.review_prompts` | `(wb) -> {sid: [{title, text, source, suggested_verdict, suggested_operation, decide_on}]}` | review UI: subject page (bundle `prompts`) |

Signals and findings are computed once per commit, so they may not read
decisions: a signal's `ctx` has `wb`, `subjects` and `labels`, nothing else.
The four review UI hooks also run only at snapshot time (`snapshot.review_hooks`);
while they run, `wb.snapshot_view` is `{subjects, labels, signals}` (signals
unmasked, no decisions). Their output rides in the bundle, so derive, the
daemon's request handlers and the hosted API run no plugin code.

**Hosted mode** (plugins run only in the isolated runner, and only from a
plugin tree a superadmin approved): `plugin_dir` is required when `plugins`
is non-empty; each plugin must be a module file directly in it, and helper
modules it imports must sit beside it (and be imported at module top level);
every config path must stay inside the repo; `apply:`, `wb.routes` and
`wb.regen` are ignored with a warning. Plugins may import only the standard
library, `yaml`, `ruamel.yaml` and `curation_workbench`.

## Review UI

A React app (`frontend/`, the house stack: React 19, react-router 7, Vite 8)
that `cwb serve` serves from the same port as its API. Build it once (needs
Node >= 20.19); the build lands in `src/curation_workbench/web/` (gitignored,
shipped as package data), so running it needs no Node:

```bash
npm --prefix frontend ci && npm --prefix frontend run build   # style guard + tsc + vite
cwb -c workbench.yaml serve                                    # open http://127.0.0.1:8781/
npm --prefix frontend run dev        # dev loop: Vite on :5190, proxies /api to the daemon
npm --prefix frontend test           # Vitest
```

Menu: **Review** (one card per review type; each type page lists its items
with one-click answers), **Apply** (pending decisions, dry run, apply all),
**More** (History, All items, Findings, and the project's `more_links`). A
subject page has the full decision form. Without a build the daemon serves
the API only and says so.

The browser has no answer logic and names no project: `cwb review` builds
every item (title, plain-language reasons, choices, ready-made answers) in
`answers.py` from the snapshot bundle, and the daemon's read routes return
bodies built by `views.py` (the hosted API calls the same functions). One
bundle serves both modes, picked at run time from `GET /api/config`; hosted
mode (votes, tallies, curator decisions, team admin) talks only through
`frontend/src/api/hosted.ts`. The hosted service (`cwb hosted serve`) serves
the same `web/` folder with an SPA fallback; for hosted dev run Vite with
`VITE_DEV_BACKEND=http://127.0.0.1:8134`.

### review_types.yaml (version 2)

```yaml
version: 2                 # required; version 1 files are refused
title: MyProject           # display name; `{project}` in default answer rationales
reasons:                   # finding rule -> plain words on review rows
  unmapped-high-usage:
    pattern: 'has (?P<n>\d+) files'   # Python regex searched in the message
    text: "Used by {n:,} files."      # named groups fill it (all-digit groups become ints)
    else: "Used by many files."       # when the pattern does not match (default: raw message)
  no-mapping-at-all: {text: "Not linked.", hide_if: [unmapped-high-usage]}
  mapping-suggestion: {hide: true}
  similar-terms: {pattern: 'similarity (?P<score>[\d.]+)', text: 'Like "{other}" ({score}).'}
answers:                   # per answers_kind settings
  links:
    predicates: {so: "ex:has_type", "*": "skos:relatedMatch"}   # by suggestion source
    grades:                # sources whose Accept becomes one button per grade
      so: {field: fit, rationale: "{label} {phrase} this.",
           options: [{label: Exact fit, value: exact, phrase: is exactly}]}
more_links: [{label: Gaps, url: "https://example.org/gaps"}]
tracks: [...]              # as before: tracks, types, rules, answers_kind, fallback
```

`{other}` is the other subject a finding names, `{message}` the raw message.
A rule with no `reasons` entry shows its raw message. `answers_kind` is one of
`signoff`, `links`, `definitions`, `duplicates`, `structure`, `keep-or-cut`,
`fields`. Each answer carries `answer`, a stable key (hash of verdict,
operation, also and evidence) that hosted votes are counted by.

## Storage

Decisions go through one interface (`curation_workbench.store`) with two
backends. Both call the same validator (`store.validate`, `store.validate_vote`);
neither has rules of its own.

- **Local mode (default):** `YamlStore`, the project's `curation/decisions.yaml`.
  It mints `DEC-NNNN` ids and writes the file exactly as before. Optimistic
  concurrency is whole-file (`mtime_ns`, the daemon's `X-Store-Mtime` header).
  `cwb apply` runs only against this store.
- **Hosted mode:** `PgStore(engine, project_id)`, Postgres through psycopg 3.
  It holds projects, votes and hosted decisions only. Hosted ids are `H-NNNN`
  per project with `origin: {kind: hosted, ref: <own id>}`, so they never
  collide with a repo's `DEC-` ids. Each record and vote has a `rev` for
  compare-and-swap; the project's `decisions_rev` is the store-wide token.
  The repo's own decisions are never imported (the hosted service reads them
  from the project snapshot).

Connection: `CWB_DATABASE_URL` (`postgresql+psycopg://...`; bare
`postgresql://` is rewritten to it, `+psycopg2` is refused), else
`POSTGRES_DB/USER/PASSWORD/HOST/PORT`. See `.env.example`.

```bash
scripts/dev-db.sh start                     # local dev Postgres (cwb-dev-pg, port 55436)
export CWB_DATABASE_URL=$(scripts/dev-db.sh url)
cwb db migrate                              # apply deployment/migrations/*.sql
cwb db status                               # exit 0 only if nothing is pending, unknown or missing
cwb db project add onga --name ONGA         # create a hosted project
cwb db export --project onga --template curation/decisions.yaml -o out.yaml [--ids H-0001 ...]
scripts/test-postgres.sh                    # postgres tests in a throwaway container (port 55437)
```

**Export** (the seam for sending hosted decisions back as a pull request):
`export_yaml` appends a project's pending hosted decisions to a copy of the
repo's `decisions.yaml`, renumbered into its `DEC-` sequence; each keeps
`origin.ref` = its old `H-` id. With nothing to export the output is
byte-identical to the input.

**Migrations** live in `deployment/migrations/` (override: `CWB_MIGRATIONS_DIR`),
one numbered file each, applied once by `cwb db migrate` and recorded in
`schema_migrations`. Before the first production deploy, fix a mistake by
editing the owning file and recreating the dev database; after it, only add files.

| Numbers | Owner | Files |
|---|---|---|
| `001` | hosted service | `001_snapgate.sql` (snapgate base tables) |
| `010-019` | storage | `010_projects.sql`, `011_decisions.sql`, `012_votes.sql`, `013_decision_votes.sql` |
| `020-029` | hosted service | team members, question resolutions, user foreign keys |
| `030-039` | free | |
| `040-049` | sync worker | `040_residents_sync.sql` (residents, plugin approvals, snapshots, derived status, sync jobs, stale events) |
| `090-099` | hosted service | snapgate upgrades |

## The ONGA reproduction

`adapters/onga/` holds ONGA's `workbench.yaml`, its plugin (embeddings
findings, ENCODE usage, upstream SO requests, facet decomposition, SO
element-type rules, site-protected names, the proposals route, and the review
UI hooks: mapping suggestions, browse links, usage and upstream panels, review
prompts) and its `review_types.yaml` (version 2, with ONGA's wording). The tests reproduce ONGA's own outputs:

```bash
# read-only checks (subjects, store, lint) against an ONGA checkout:
ONGA_ROOT=../onga pytest -q
# full set, including status, apply and the daemon, needs a disposable worktree:
git -C ../onga worktree add --detach ~/work/cwb-onga HEAD
ONGA_ROOT=~/work/cwb-onga ONGA_SCRATCH=~/work/cwb-onga pytest -q
```

`tests/test_onga_review_items.py` compares every review item with the ONGA
site's own `itemsFor()` (run with `npx tsx`); it needs ONGA's uncommitted
`site/src/lib/review-types.ts` (and the files it imports) copied into the
worktree, and `site/node_modules` (a symlink to the main checkout's works).

See STATUS.md for what matches and what differs.
