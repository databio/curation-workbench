# How the website gets each project's data

The schema stays in each project's own git repo. The website only handles
curation decisions, so it needs one thing from the repo: the **snapshot**, a
JSON file that the repo builds and commits itself. The website downloads that
file from GitHub and never runs the project's code.

```
project repo (laptop or CI)                    curate.databio.org (one API process)
  make regen -> cwb regen                        every CWB_FETCH_SECONDS (300):
    runs the plugins (trusted, local)              GET api.github.com/repos/<repo>/commits/<branch>
    writes curation/snapshot.json   -- push -->    new commit? GET raw.githubusercontent.com/<repo>/<sha>/workbench.yaml
    (`snapshot:` in workbench.yaml)                            GET raw.githubusercontent.com/<repo>/<sha>/<snapshot path>
                                                   publish: Postgres cw_snapshots, mark landed decisions,
                                                            staleness pass
                                                 on each read: derive status + review from the
                                                   snapshot and the decisions (cached by rev)
```

## The snapshot file

`snapshot.build(wb)` computes everything that depends only on the repo at one
commit (subjects, crosswalk, aliases, lint, generic and plugin findings, every
queue signal, the review UI's plugin hooks) into one bundle (`cwb-snapshot/1`,
keys in `src/curation_workbench/snapshot.py`). It is the only place plugin code
runs, and it runs only on the project's own machines.

Set `snapshot: curation/snapshot.json` in `workbench.yaml` (relative to that
file). Then `cwb regen` writes it next to status.json and review.json, `cwb
snapshot` writes just it, and `cwb snapshot --check` / `cwb regen --check`
fail when it is out of date. ONGA's `make regen` writes it and `make test`
checks it. The file must be byte-stable for a given commit (signals return
sorted dicts), so the check never flaps.

`derive.status(bundle, decisions)` and `derive.review(bundle, status, next_id)`
turn a bundle plus a decision list into status.json and review.json. They are
generic code with no plugin calls and take milliseconds.
`derive.merge_decisions(repo, hosted)` puts the repo's decisions first and
drops any hosted one the repo already holds (a repo record with
`origin: {kind: hosted, ref: <H- id>}`). With no hosted decisions, the site's
review equals the repo's committed review.json except `decisions_next_id`
(`tests/test_onga.py::test_site_review_from_the_committed_snapshot`).

## Trust model

- The site imports and runs no project code. It reads `workbench.yaml` as data
  (`yaml.safe_load`) and the snapshot as JSON, checks the format, and stores it
  (`tests/test_module_boundaries.py` checks that the API never loads
  `curation_workbench.workbench`, the only module that imports plugins).
- No git, no clone, no credentials: public repos over plain HTTPS
  (`hosted/sync/github.py`). The server holds no GitHub token at all.
- The `snapshot:` path must stay inside the repo; downloads are size-capped
  (1 MiB for workbench.yaml, 64 MiB for the snapshot).
- Only a superadmin registers a project (the admin form on the home page, or
  `cwb hosted project add` in the API container). API keys are never admin.

## Tables (`041_repo_snapshots.sql`)

| Table | Holds |
|---|---|
| `cw_repos` | per project: `repo` (`owner/name` on github.com), `branch`, `current_snapshot_id`, `last_commit`, `last_checked_at`, `last_error` |
| `cw_snapshots` | one row per (project, commit): the gzipped bundle, its sha256, fingerprint, warnings |
| `cw_stale_events` | append-only log of every stale mark and clear |

Snapshots are never deleted: stale marks on votes and decisions point at the
snapshot that made them stale. There is no derived-data table and no job
queue: `hosted/project_data.py` derives status and review on read and caches
them in memory by (snapshot id, `projects.decisions_rev`).

## Fetching (`hosted/sync/repos.py`)

`sync_project(engine, slug)` asks GitHub for the branch head. Same commit as
the current snapshot: it only records `last_checked_at`. New commit: it reads
`workbench.yaml` at that commit, takes `snapshot:`, downloads the file and
calls `publish_snapshot`. Any failure is stored in `last_error` and the last
good snapshot stays live. `Poller` runs `sync_all` every `CWB_FETCH_SECONDS`
(default 300 in production, off elsewhere) in a daemon thread of the API
process. A superadmin can fetch at once with **Fetch now** on the home page or
`cwb hosted project sync <slug>`. Unauthenticated GitHub API calls are limited
to 60 an hour per IP; one check per project every 5 minutes is 12.

Projects whose repo is `dev:<label>` (made by `cwb hosted admin dev-snapshot`,
dev and tests only) are never fetched.

## The publish transaction

`hosted.sync.publish.publish_snapshot(conn, project_id, bundle, commit_sha)`,
in one transaction:

1. lock the project's `cw_repos` row; insert the `cw_snapshots` row (or reuse
   the one for the same commit);
2. `mark_landed`: every pending hosted decision whose id appears as an
   `origin.ref` in `bundle.repo_decisions` is marked applied with the repo
   record's `applied` block plus `repo_id` and `commit` (a non-terminal record
   such as `defer` stays pending in the repo; its hosted copy gets just the
   date). This runs before the staleness pass, so a decision is never marked
   stale by its own change;
3. the staleness pass;
4. make the snapshot current and clear `last_error`.

## Staleness rules

Against the bundle's subjects and aliases (alias chains followed), for every
open vote and every pending hosted decision (each `subject_hashes` entry; the
first failing one gives the reason):

- resolves to nothing, or to a retired subject: `subject_removed`;
- renamed (resolves to another SID): `renamed`, with `new_sid`;
- hash differs: `subject_changed`;
- otherwise a mark is cleared (a revert brought the content back).

Applied and withdrawn decisions and the repo's own decisions are never
touched. Marks go through `PgStore.set_stale` / `clear_stale` (they never bump
`decisions_rev`), every change appends a `cw_stale_events` row, and nothing is
deleted. A voter who votes again clears the mark. The pass is idempotent and
runs on every publish and after every vote or decision write
(`publish.refresh_stale`, in the write's transaction).

Findings files whose fingerprint does not match (for example embeddings
reports not regenerated after a schema change) show as stale sources in the
bundle and as warnings ("... is from an older schema").

## Registering a project

```bash
cwb hosted project add onga --name ONGA --repo https://github.com/databio/onga --branch master
cwb hosted project list            # repo, commit, last fetch, error
cwb hosted project show onga       # plus the snapshot's plugins and findings sources
cwb hosted project sync onga       # fetch now
```

Or the **Add a project** form on the home page (superadmin). Both refuse a
repo GitHub cannot read, then fetch once; a snapshot that fails to load is
reported as the project's last error.

## How approved decisions reach the repo (and get marked applied)

The service never holds git credentials. The project repo pulls:

1. A GitHub Action in the project repo (ONGA: `.github/workflows/curate.yml`)
   runs `cwb pull <slug>` with a `curation:read` key (repo secret
   `CURATE_API_KEY`) and opens a pull request with its own `GITHUB_TOKEN`.
2. `cwb pull` reads `GET /api/v1/projects/<slug>/decisions/export`: pending
   hosted decisions that are not stale (the rest come back in `held`). It
   appends them to the checkout's `decisions.yaml` with
   `store.landing.append_hosted` (renumbered to `DEC-`, `origin: {kind: hosted,
   ref: <H- id>}`) and applies them with the normal apply engine, which runs
   the project's own checks, all or nothing. The apply's `make regen` also
   rewrites the snapshot file.
3. After the merge, the site's next fetch publishes the new snapshot, and
   `mark_landed` marks those decisions applied.
