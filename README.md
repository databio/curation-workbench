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
```

## Commands

```bash
cwb -c workbench.yaml subjects [--check]   # subjects.json, term_crosswalk.json, subject_aliases.json
cwb -c workbench.yaml lint [--check]       # findings/schema_lint.json
cwb -c workbench.yaml status [--check]     # status.json: state + queue score per subject
cwb -c workbench.yaml review [--check]     # review.json: queue sorted into review tracks/types
cwb -c workbench.yaml regen [--check]      # all of the above, in order
cwb -c workbench.yaml apply [--dry-run] [--ids DEC-0001 ...] [--emit pending.json]
cwb -c workbench.yaml serve [--port 8781]  # local decision daemon (loopback only)
```

Global flags: `--root` (the project tree; overrides the config) and `--out`
(where generated files go; default the curation dir).

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
| `040-049` | sync worker | residents, snapshots, derived status, sync jobs, stale events |
| `090-099` | hosted service | snapgate upgrades |

## The ONGA reproduction

`adapters/onga/` holds ONGA's `workbench.yaml`, its plugin (embeddings
findings, ENCODE usage, upstream SO requests, facet decomposition, SO
element-type rules, site-protected names, the proposals route) and its
`review_types.yaml`. The tests reproduce ONGA's own outputs:

```bash
# read-only checks (subjects, store, lint) against an ONGA checkout:
ONGA_ROOT=../onga pytest -q
# full set, including status, apply and the daemon, needs a disposable worktree:
git -C ../onga worktree add --detach ~/work/cwb-onga HEAD
ONGA_ROOT=~/work/cwb-onga ONGA_SCRATCH=~/work/cwb-onga pytest -q
```

See STATUS.md for what matches and what differs.
