# Status (2026-10-21)

Rebuild of ONGA's curation workbench as a standalone package. Each stage below
was checked against ONGA's own code and outputs by reproduction tests; their
final run was against ONGA `6b38215` (2026-10-21), after which ONGA switched
onto this package and those tests were retired (see "ONGA switchover").

| Stage | What | Reproduces ONGA? |
|---|---|---|
| 1 | Subject registry: subjects.json, term_crosswalk.json, subject_aliases.json | Byte-identical (except `_generated_by`) |
| 2 | Decision store + validation | Same accept/reject (incl. error text) on 26 crafted records and all 158 stored ones; create/update/withdraw write byte-identical files |
| 3 | Status + queue (pluggable signals) | Byte-identical status.json, at HEAD and with live pending decisions |
| 4 | Schema lint (generic rules + ONGA facet rules) | Byte-identical schema_lint.json |
| 5 | Apply engine + 63 handlers (45 generic, 2 ONGA-only, 16 record-only) | Dry run: identical diffs and effects for 32 test decisions, one at a time and together (27 apply, 4 refused alike, 1 refused at record time alike). Real apply of 3 decisions (with ONGA's project_mappings, make regen, make test): identical tree diff |
| 6 | Daemon (same routes) + review types as data | Same status codes and bodies for a 14-request sequence; review.json counts equal the ONGA site's queue tabs |

Also: a toy project with no plugins (`tests/fixtures/toy`) runs regen,
decisions and a real apply.

## One version (2026-10-21)

There is only the website (curate.databio.org). Local mode is gone: the
loopback daemon (`cwb serve`, `server.py`), the `wb.routes` hook, the SPA's
local backend and mode, and `YamlStore`'s create/update/withdraw. Stage 6 above
(the daemon) is therefore history; its tests were retired. decisions.yaml is
now only where decisions land: `cwb pull <project>` fetches approved decisions
(`GET .../decisions/export`, read-only key), appends them renumbered to `DEC-`
(`store.landing.append_hosted`), and applies them; ONGA's GitHub Action
(`.github/workflows/curate.yml`) runs it and opens a pull request with
`GITHUB_TOKEN`. On the next sync, `publish.mark_landed` marks those hosted
decisions applied. The website's Apply page became **Approved** (waiting
decisions, held-back ones flagged, how they get to the repo, the Actions link,
the last-synced commit, recently landed ones, and a curator-only button that
makes the read-only key). The review home is shorter (cards: title, count,
question; the per-type `answers:` preview field is gone from review_types.yaml).
Checked end to end on a laptop (see the jot plan `single_version`).
Prerequisites before ONGA's Action can run: curation-workbench public, ONGA
allowing Actions to create pull requests, and the `CURATE_API_KEY` secret.

Storage seam (2026-10-21): decision storage is one interface with two
backends, the local YAML file (unchanged output, still checked by the tests
above) and Postgres for the hosted version (projects, votes, hosted `H-`
decisions, migrations 010-013, export back to `decisions.yaml`). Both run the
same contract tests (`tests/test_store_contract.py`); Postgres-only behavior is
in `tests/test_pg_store.py` (`scripts/test-postgres.sh`). `cwb apply` works
only on decisions.yaml: hosted decisions reach it through `cwb pull`.

Sync worker (2026-10-21): regen is split into `snapshot` (everything that
depends on the repo at one commit; the only place plugin code runs) and
`derive` (status/review from a bundle plus decisions; generic code).
`cwb regen` output is unchanged (byte-identical status.json, and review.json
counts as before). Hosted mode adds resident projects (`cw_residents`, migration
040), a sync worker that mirrors each resident's repo, builds one snapshot
per commit in an isolated runner (`cwb runner`, plugins only from a
superadmin-approved git tree), publishes it to Postgres with the derived
status/review, and marks votes and pending hosted decisions stale when their
subject changed. ONGA through the hosted path (its tree as the worker sends
it, plugin_dir `curation/plugin`, runner in a child process)
derives the same status.json and review.json as `cwb regen`
(`tests/test_onga_hosted.py`). Not yet exercised: the runner inside Docker
with Swarm isolation (`cwb hosted isolation-check` there), a real GitHub
remote and webhook. See dev-docs/sync.md.

Deploy pieces (2026-10-21): `deployment/` has the Dockerfile (one image for
the API, worker and runner), `healthcheck.py`, `production.env` (pass
references), `verify.sh` and the runbook `deploy.md`; the Swarm stack
(`stacks/curate.yml`), `deploy.sh curate` wiring and the nginx block are in
`cloud-management/gcp/compute`. The image was built and run locally against a
throwaway Postgres (migrate, API, runner on an internal network, worker; the
isolation check ends `ISOLATED` in a read-only, no-capability runner container;
ONGA at its head snapshots in under 3 s; peaks: API 121 MiB, worker 165 MiB,
runner 87 MiB). Not yet done (Nathan): the go-live steps in `deployment/deploy.md`
(pass entries, database, Prosopia client, secret, nginx, DNS, first deploy).

## Behavior differences

- **Protected-name refusals** read `X is protected (named in site/scripts/build-data.js); ...`
  instead of ONGA's per-handler wording. The refusals happen in the same cases.
- **Origin vocabulary** gains `hosted`, so the "origin.kind must be one of [...]"
  error lists it; ONGA's store does not. The store test allows exactly this.
- **Lint rules missing from policy.yaml are switched off** instead of crashing
  with a KeyError. ONGA configures every rule it emits, so its output is unchanged.
- **Usage lint rules** (`zero-usage-term`, `unmapped-high-usage`) treat a term
  missing from the usage data as 0 instead of raising.
- **review.json** closes a suggestion whose object is an IRI when the term has
  an SSSOM row for the same object as a CURIE (option `iri_prefixes`), as the
  ONGA site's TypeScript did. status.json keeps ONGA's exact (raw) comparison.

## ONGA switchover (2026-10-21)

ONGA now uses this package: its `workbench.yaml` (repo root), plugin
(`curation/plugin/onga_plugin.py` + `onga_mappings.py`) and
`curation/review_types.yaml` live in the ONGA repo; `adapters/` is gone. ONGA's
own engine (`scripts/workbench/` and five scripts) is deleted and its Makefile
calls `cwb`; `make curate` ran `cwb serve` (now `make curate-pull`, see "One
version"), and ONGA's Astro
queue, pending and log pages are gone (its develop pages are read-only). After
the switch ONGA regenerates byte-identical files apart from `_generated_by`
(plus the new `curation/review.json`), and its `make test` is the guard.
`tests/test_onga.py` runs the `--check`s, the review UI's read bodies and an
apply dry run against an ONGA tree.

Final reproduction run (ONGA `6b38215`, before the switch): all stage tests
passed, and every review item equalled the ONGA site's `itemsFor()` once the
worktree's site data was rebuilt; the only failure was the hard-coded review
counts, which predated two keep decisions.

## Where ONGA-specific code went

All of it is in ONGA's `curation/plugin/onga_plugin.py` (helpers in
`onga_mappings.py`): legacy reference forms,
embeddings findings, ENCODE usage, upstream SO requests (signal, label
references, the PATCH /api/proposals route), facet decomposition (lint rules,
label references, the `facet` handler), scope delegations, SO element-type
rules (validation, SSSOM row checks and columns, so.obo labels), the
`promote_tier` handler and its tier-schema check, the move-between-content-enums
check, and names protected by build-data.js and the Makefile. Settings that
were constants (adoption root, FGA source prefix, content enums, layer
annotation, id scheme, examples dir) are now options in `workbench.yaml`.

## Next

- Review UI (2026-10-21): a React app in `frontend/`, served by `cwb hosted
  serve` (Review / Approved / More, type pages with one-click answers, subject
  page with the full decision form). Items are built in Python (`answers.py`,
  review.json version 2).
- Register ONGA as the first resident (dev-docs/sync.md).
- Hosted service (2026-10-21): `cwb hosted serve`, a FastAPI app with
  Prosopia sign-in through vendored snapgate 5.1.0, invite-only accounts
  (pending until a superadmin approves), per-project teams (voter, curator),
  votes and tallies, curators resolving a question into an `H-` decision
  (linked votes, tally as evidence) or no action, team and project admin, and
  the read routes (`views`). It reads plan 4's
  published snapshots and never runs plugin code; `cwb hosted admin
  dev-snapshot` publishes a local folder in dev. Routes: `hosted/API.md`;
  tests: `tests/hosted/` (postgres tier). Checked end to end locally with
  demo sign-in in headless Chromium (toy project and ONGA). Still to do:
  deployment at curate.databio.org (plan 5) and the Prosopia `curate` client.
- (Done earlier: votes and hosted decisions in Postgres; the sync worker,
  runner and resident projects.)
- (Done: ONGA's `curation/verdicts.yaml` is readable by PyYAML; the three
  flow-map values ending in `?` are quoted.)
