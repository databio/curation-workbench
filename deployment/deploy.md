# Deploying the curation workbench on goat

The hosted curation workbench runs at `https://curate.databio.org` on goat, the
lab's single-node Docker Swarm server, the same way Pigeon does. This file is
the runbook. Git stays the source of truth for each project's schema; this
service holds only curation state (people, teams, votes, decisions) and never
writes to a project's repo.

## What runs

Stack `curate`, one image (`deployment/Dockerfile`), one service:

| Service (on goat) | Does | Notes |
|---|---|---|
| `curate_curate` | API + web app (`cwb hosted serve`, port 8134) | Runs `cwb db migrate` at start, then serves. One replica (rate limits are in-process). A background thread checks each project's GitHub repo every `CWB_FETCH_SECONDS` (300) and loads its committed snapshot file. |

Each project repo builds and commits its own snapshot file (`cwb regen`,
`snapshot:` in its workbench.yaml). The service downloads that file from
GitHub over plain HTTPS (public repos, no token) and never runs project code,
so there is no worker, no runner and no plugin approval step
(`dev-docs/sync.md`).

One secret, `curate_env`, built from `deployment/production.env`, is mounted in
the API. Data is in the shared goat Postgres (database `curate`, covered by
the weekly `pg_dumpall` backup), behind `goat_nginx` and Cloudflare. The stack
file, `deploy.sh` wiring and nginx block live in `cloud-management/gcp/compute`
(`stacks/curate.yml`, `bin/deploy.sh`, `services/nginx/nginx.conf`).

The first deploy (database, Prosopia client, secret, nginx, DNS) is done; it is
recorded in the git history of this file.

## Routine deploy

From `cloud-management/gcp/compute`: `bin/deploy.sh curate` (build, push,
roll out stop-first, run `verify.sh`, fail the deploy on any failed check).
`bin/deploy.sh curate --no-build` redeploys the image already in the registry.
The deploy builds from the local checkout of `pepkit/repos/curation-workbench`,
so commit first.

## After a deploy

`verify.sh` runs by itself. To run it by hand:

```bash
deployment/verify.sh https://curate.databio.org
CWB_VERIFY_SKIP_SSH=1 deployment/verify.sh https://curate.databio.org   # no ssh to goat: those checks SKIP
```

Environment variables are listed at the top of `verify.sh`. It sends only GET
requests and runs read-only commands: `cwb db status`, and `cwb hosted project
list`, which must show every registered project with a commit and no error.

## Running a command in a task

The CLI needs the secret's environment, so source it inside the container:

```bash
ssh $GOAT
cid=$(docker ps -q -f label=com.docker.swarm.service.name=curate_curate -f health=healthy | head -1)
docker exec -it "$cid" sh -c 'set -a && . /run/secrets/curate_env && set +a && cwb hosted project list'
```

## First admin and accounts

Nathan is the superadmin through `SNAPGATE_SUPERADMIN_EMAILS`. New sign-ups are
pending; approve them at `/admin/accounts` (snapgate's user admin), then add
them to a project's team (the project's Team page, or `cwb hosted admin
add-member`).

## Registering a project

The project's repo must be public on GitHub, set `snapshot:` in its root
`workbench.yaml`, and commit that file (ONGA: `curation/snapshot.json`, written
by `make regen`). Then, as superadmin, use **Add a project** on the home page
(short name, name, repo URL, branch), or in the API task:

```bash
cwb hosted project add onga --name ONGA --repo https://github.com/databio/onga --branch master
cwb hosted project list        # each project: repo, commit, last error
cwb hosted project sync onga   # fetch now instead of waiting for the next check
```

## How decisions reach a project's repo

The service holds no git credential. Each project repo pulls approved
decisions itself: a GitHub Action in the repo (ONGA's
`.github/workflows/curate.yml`) runs `cwb pull` with a read-only curate API key
(repo secret `CURATE_API_KEY`, made by a curator on the project's Approved page)
and opens a pull request with GitHub's own token. After the merge, the site's
next fetch loads the new snapshot and marks those decisions applied. Nothing
to deploy for this on goat.

## Rotating secrets

Change the value in `pass`, then `bin/deploy.sh --push-secrets curate`
(restarts the API, which mounts `curate_env`).

## Logs

```bash
docker service logs -f --tail 200 curate_curate     # includes one `fetch <slug> ...` line per check
docker service ps curate_curate
```

## Migrations

`cwb db migrate` runs only in the API service, before `cwb hosted serve`, under
a Postgres advisory lock. Files already applied on goat (001, 010-013, 020-022,
040, 090) are frozen: schema changes only add new numbered files (041 replaced
040's worker tables with `cw_repos`, `cw_snapshots` and `cw_stale_events`).

## Local image test

```bash
docker build -t curate:dev -f deployment/Dockerfile .
```

Run it as `sh -c 'cwb db migrate && exec cwb hosted serve --host 0.0.0.0 --port 8134'`
against a throwaway Postgres.
