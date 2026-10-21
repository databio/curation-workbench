# Resident projects and the sync worker

In hosted mode the schema stays in each project's own git repo; the service
only handles curation decisions. So the server keeps its own copy of each
project's repo and rebuilds the generated data whenever a new commit lands.
A **resident** is the registration record for one project's repo; the
**sync worker** watches residents and publishes their data to Postgres, where
the API reads it.

```
GitHub repo ──ls-remote/fetch──> worker (cwb hosted worker) ──tar──> runner (cwb runner)
   ^  push webhook (optional)         |  holds DB secret,          no secrets, no network,
   |                                  |  GitHub read access        read-only root, tmpfs
API ── request_derive ───────────────>|                            runs all repo code
                                      |<──── snapshot bundle ───── incl. plugins
                                      v
                      Postgres: cw_residents, cw_snapshots, cw_derived,
                      cw_sync_jobs, cw_stale_events, cw_plugin_approvals
                      + stale marks on votes/decisions
```

## Two phases: snapshot and derive

`snapshot.build(wb)` computes everything that depends only on the repo at one
commit (subjects, crosswalk, aliases, lint, generic and plugin findings, every
queue signal) into one JSON bundle (`cwb-snapshot/1`, keys in
`src/curation_workbench/snapshot.py`). It is the only place plugin code runs.

`derive.status(bundle, decisions)` and `derive.review(bundle, status, next_id)`
turn a bundle plus a decision list into status.json and review.json. They are
generic code with no plugin calls, so the worker runs them on every decision
change in milliseconds. `derive.merge_decisions(repo, hosted)` puts the repo's
decisions first and drops any hosted one the repo already holds (a repo record
with `origin: {kind: hosted, ref: <H- id>}`).

Signals may not read decisions. The one decision-dependent signal, `port`, is
a *masked* signal: the bundle carries its raw values and derive zeroes it
where a terminal decision exists.

Local mode uses the same pair: `cwb regen` is snapshot then derive; the daemon
caches the bundle and re-derives status per request.

## Trust model

- Only a superadmin registers a resident (`cwb hosted resident add`, as the
  operator inside the API or worker container). API keys are never admin.
- Plugin code runs only if its git tree id (of `plugin_dir`) matches one a
  superadmin approved after reading it (`plugin-diff`, `approve-plugin --tree`).
  The approved files are stored in `cw_plugin_approvals`, so the worker never
  depends on the object still being in the remote.
- When a commit changes the plugin tree, the worker keeps running the last
  approved files on the new commit and warns `plugin code changed at <sha>
  (tree <tree>); still running approved tree <approved>`. With no approval it
  runs generic findings only and warns `plugin not approved; generic findings
  only`. `revoke-plugin` makes every earlier approval inactive.
- Repo code (generic or plugin) runs only in the runner, a separate service
  with no secrets, no route out, a read-only root, a tmpfs /tmp, no
  capabilities and a non-root user. Each job runs in a child process in a
  fresh /tmp folder, killed at `CWB_RUNNER_TIMEOUT`.
- In hosted mode (`Workbench.open(..., hosted=True)`) every config path must
  stay inside the checkout, symlinks out are refused, plugins import only from
  `plugin_dir`, and `apply:`, `wb.routes` and `wb.regen` are ignored.
- The worker never checks out a working tree and never runs repo code: it
  lists heads, fetches, and exports commits with `git archive`, rewritten in
  Python (no `.git*`, no absolute paths, no `..`, no symlinks out, size cap).
- A production worker refuses to start without `CWB_RUNNER_URL`.

## Jobs (`cw_sync_jobs`)

One live (`queued` or `running`) job per resident and kind (partial unique
index); a second enqueue is a no-op. Claimed with `FOR UPDATE SKIP LOCKED`,
commit before checks before derive, then oldest. Stuck jobs (running longer
than `CWB_WORKER_STUCK_SECONDS`) go back to queued, and fail after
`CWB_WORKER_MAX_ATTEMPTS`.

| Kind | Trigger | What it does |
|---|---|---|
| `commit` | poll, webhook, manual | fetch; resolve the branch head (a webhook's sha is never trusted); pick the plugin tree; reuse the snapshot for (commit, tree) or archive and run the runner's `snapshot`; publish |
| `derive` | decisions | status/review from the current bundle and decisions, plus the staleness pass; no runner |
| `checks` | decisions, manual | the plugins' decision checks on pending hosted decisions, in the runner (`mode: checks`); stored as `cw_derived.decision_checks` |

The loop (every `CWB_WORKER_TICK_SECONDS`): recover stuck jobs; poll residents
due for a check (`ls-remote`; a head unlike the current snapshot's commit
queues `commit`); queue `derive` when `projects.decisions_rev` differs from
`cw_derived.decisions_version`, and `checks` when decision hooks exist and the
rev has held still for 5 s; claim and run one job. The API also calls
`hosted.sync.request_derive(conn, project_id)` after each write.

A failed commit job leaves the current snapshot live and sets
`cw_residents.last_error`. The last archive per resident is kept at
`$CWB_SYNC_DIR/trees/<resident>/<snapshot>.tar` for checks jobs.

## The publish transaction

`hosted.sync.publish.publish_snapshot(conn, resident_id, bundle, commit_sha,
plugin_tree_used=None)`, in one transaction:

1. lock the resident row; insert the `cw_snapshots` row (or reuse the one for
   the same commit and plugin tree);
2. the staleness pass;
3. derive status and review from the bundle and
   `merge_decisions(bundle.repo_decisions, store.records())`, and overwrite
   `cw_derived` with the `decisions_rev` read in the same transaction (a new
   snapshot also resets `decision_checks`);
4. make the snapshot current and clear `last_error`; queue `checks` when the
   bundle's plugins have decision hooks.

Plan 2's dev stand-in publishes through the same function to a disabled
`dev:<path>` resident (`residents.ensure_dev_resident`).

`cw_derived.status` and `.review` are `json`, not `jsonb`, so key order
survives (rollups, subjects).

## Staleness rules

Against the bundle's subjects and aliases (alias chains followed), for every
open vote and every pending hosted decision (each `subject_hashes` entry; the
first failing one gives the reason):

- resolves to nothing, or to a retired subject: `subject_removed`;
- renamed (resolves to another SID): `renamed`, with `new_sid`;
- hash differs: `subject_changed`;
- otherwise a mark is cleared (a revert brought the content back).

Applied and withdrawn decisions and the repo's own decisions are never
touched. Marks go through plan 1's `set_stale` / `clear_stale` (they never
bump `decisions_rev`), every change appends a `cw_stale_events` row, and
nothing is deleted. A voter who votes again clears the mark. The pass is
idempotent and runs in full on every publish and every derive (so a vote cast
against a hash that a just-published snapshot changed is caught by the next
derive).

Findings files whose fingerprint does not match (for example embeddings
reports not regenerated after a schema change) show as stale sources in the
bundle and as warnings ("... is from an older schema").

## Resident admin

```bash
cwb db project add onga --name ONGA
cwb hosted resident add --project onga --repo https://github.com/databio/onga \
    --branch master --config workbench.yaml --plugin-dir curation/plugin
cwb hosted resident plugin-diff onga        # read the plugin code
cwb hosted resident approve-plugin onga --tree <tree printed by plugin-diff>
cwb hosted resident sync onga --wait
cwb hosted resident show onga               # snapshot, plugins, warnings, jobs, plugin status
cwb hosted resident check onga              # rebuild the commit in the runner and compare
```

`add` checks the URL (https on `CWB_ALLOWED_GIT_HOSTS`), runs one
`ls-remote`, and prints the webhook secret once. There is no remove: disable
instead. The webhook (`POST /api/v1/hooks/github/{slug}`, mounted by the API)
checks `X-Hub-Signature-256`, answers `ping`, ignores other branches, and
queues a commit job; polling every 300 s works without it.

### Registering ONGA (after plan 6 moves its adapter into the ONGA repo)

1. ONGA's `workbench.yaml` at the root with `plugin_dir: curation/plugin`, the
   plugin and its helpers in `curation/plugin/`, review types in
   `curation/review_types.yaml`, pushed to `master`.
2. Run the commands above in the deployed container. `show` must report a
   current snapshot at ONGA's head, `onga_plugin` on the approved tree, the
   fingerprint of ONGA's committed `curation/subjects.json`, embeddings sources
   not stale, and no `last_error`; `check` must print MATCH.
3. Optionally add the push webhook in GitHub (content type JSON, the secret
   `add` printed).

Checked locally: `tests/test_onga_hosted.py` runs ONGA's tree (git archive of
a scratch worktree, adapter in the plan 6 layout) through the runner in a
child process with hosted rules, and the derived status.json and review.json
equal `cwb regen`'s. ONGA's `live_so_terms` already returns None without the
gitignored `so.obo`, so hosted `checks` jobs skip that check instead of failing.

## Deployment settings (input to the deploy plan)

Same image as the API, stack `curate`. The image needs `git`, and
`/var/lib/cwb` owned by uid 1000.

- **`curate-worker`** (`cwb hosted worker`): secret `curate_env` (Postgres,
  optional `CWB_GITHUB_READ_TOKEN`); networks `databio_net` (Postgres, no
  alias) and the stack's internal `run_net` (on goat `curate_run_net`);
  outbound internet for GitHub; named volume `curate_sync_data` at
  `/var/lib/cwb`; one replica; never migrates (waits for `cwb db migrate`,
  run by the API service, then prints `cwb worker started`). Env:
  `CWB_ENV=production`, `CWB_RUNNER_URL=http://curate-runner:8150`,
  `CWB_SYNC_DIR`, `CWB_WORKER_TICK_SECONDS` (10), `CWB_WORKER_STUCK_SECONDS`
  (900), `CWB_WORKER_MAX_ATTEMPTS` (3), `CWB_MAX_TREE_BYTES` (200 MiB),
  `CWB_ALLOWED_GIT_HOSTS` (github.com), `CWB_RUNNER_TIMEOUT` (300).
- **`curate-runner`** (`cwb runner --port 8150`): no secrets, no `curate_env`,
  only `run_net` (internal overlay, no route out), read-only root, 256 MiB
  tmpfs `/tmp`, uid 1000, all capabilities dropped, memory 512 MiB (1 GiB max), CPU 1,
  pids 256, one replica. Env: `CWB_RUNNER_TIMEOUT`, `CWB_MAX_TREE_BYTES`.
- `verify.sh` should check: worker and runner 1/1; the runner has no secrets,
  a read-only root, no capabilities and only `curate_run_net`; over ssh,
  `cwb hosted resident list` in the API task shows each enabled resident with
  a current snapshot and no error (SKIP when there are none).
- Runbook: `docker service logs curate_curate-worker` (one line per job:
  resident, kind, trigger, sha, outcome, seconds; never a secret),
  `cwb hosted resident show <slug>`, and `cwb hosted isolation-check` in the
  worker task (self-check plus a probe plugin that tries to read
  `/run/secrets/curate_env`, reach the internet and write outside /tmp; must
  end `ISOLATED`).

Not yet exercised: the runner inside Docker with these limits (the
isolation check was only run on a laptop, where it correctly says NOT
ISOLATED), a real GitHub remote, and the webhook behind nginx.

## The seam for "apply approved decisions and open a pull request"

Not built; what exists for it:

- `derive.merge_decisions` already drops hosted decisions that appear in the
  repo's `decisions.yaml` with `origin: {kind: hosted, ref: <H- id>}`, so after
  a PR that lands them is merged, the next sync treats them as repo history.
- The runner reserves `mode: "apply"` (400 today).
- `cw_derived.decision_checks` answers "is this decision valid against the
  plugin", which the apply step will require.

The future step: `hosted/sync/apply_seam.py` with `ApplyRequest(resident_id,
base_snapshot_id, decision_ids, requested_by)` and a job kind `apply`;
preconditions: the base snapshot is current and every decision is approved,
not stale, with empty checks. The runner's apply mode takes the tree at the
base commit plus `decisions.yaml` with the chosen decisions appended by
`export_yaml` (renumbered to `DEC-`, `origin.ref` = the `H-` id), runs the
apply engine with `--emit`, and returns the staged changes without running the
config's `apply:` shell commands. A separate publisher with a write token the
worker never has pushes `cwb/apply-<id>` and opens the PR; after merge, the
normal sync and `merge_decisions` close the loop and the worker marks the
hosted decisions applied (`PgStore.mark_applied`, matched by `origin.ref`).
