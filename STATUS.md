# Status (2026-10-21)

Rebuild of ONGA's curation workbench as a standalone package, with ONGA as a
configured project (`adapters/onga/`). Each stage is checked against ONGA's own
code and outputs (`tests/`, 11 ONGA and toy tests, ~2 min). Nothing in the ONGA repo was
changed.

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

Storage seam (2026-10-21): decision storage is one interface with two
backends, the local YAML file (unchanged output, still checked by the tests
above) and Postgres for the hosted version (projects, votes, hosted `H-`
decisions, migrations 010-013, export back to `decisions.yaml`). Both run the
same contract tests (`tests/test_store_contract.py`); Postgres-only behavior is
in `tests/test_pg_store.py` (`scripts/test-postgres.sh`). `cwb apply` is
local-only by design: hosted decisions reach the repo through export.

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

## Where ONGA-specific code went

All of it is in `adapters/onga/onga_plugin.py`: legacy reference forms,
embeddings findings, ENCODE usage, upstream SO requests (signal, label
references, the PATCH /api/proposals route), facet decomposition (lint rules,
label references, the `facet` handler), scope delegations, SO element-type
rules (validation, SSSOM row checks and columns, so.obo labels), the
`promote_tier` handler and its tier-schema check, the move-between-content-enums
check, and names protected by build-data.js and the Makefile. Settings that
were constants (adoption root, FGA source prefix, content enums, layer
annotation, id scheme, examples dir) are now options in `workbench.yaml`.

## Next

- A review UI that reads `review.json` + the daemon (the ONGA site's queue pages
  are the model; today they live in ONGA's Astro site).
- Point ONGA at this package: move `adapters/onga/` into the ONGA repo, replace
  its scripts/workbench and the five scripts with `cwb`, and switch its Makefile.
- The hosted multi-user version: Prosopia sign-in, teams, the API, the sync
  worker and deployment. (Done: votes and hosted decisions in Postgres.)
- ONGA's `curation/verdicts.yaml` cannot be read by PyYAML (a flow map at line
  255 uses `?`); ruamel reads it. Harmless today, but worth fixing in ONGA.
