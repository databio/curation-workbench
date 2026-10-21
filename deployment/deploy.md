# Deploying the curation workbench on goat

The hosted curation workbench runs at `https://curate.databio.org` on goat, the
lab's single-node Docker Swarm server, the same way Pigeon does. This file is
the runbook. Git stays the source of truth for each project's schema; this
service holds only curation state (people, teams, votes, decisions).

## What runs

Stack `curate`, one image (`deployment/Dockerfile`), three services:

| Service (on goat) | Does | Notes |
|---|---|---|
| `curate_curate` | API + web app (`cwb hosted serve`, port 8134) | Runs `cwb db migrate` at start, then serves. One replica (rate limits are in-process). |
| `curate_curate-worker` | Sync worker (`cwb hosted worker`) | Mirrors project repos from GitHub, publishes snapshots to Postgres. Never migrates; waits for the API's migrations. Volume `curate_curate_sync_data` (derived data only). |
| `curate_curate-runner` | Runs project plugin code (`cwb runner --port 8150`) | No secrets, no route out, read-only root, all capabilities dropped. On the internal network `curate_run_net` only. |

One secret, `curate_env`, built from `deployment/production.env`, is mounted in
the API and the worker (never the runner). Data is in the shared goat Postgres
(database `curate`, covered by the weekly `pg_dumpall` backup), behind
`goat_nginx` and Cloudflare. The stack file, `deploy.sh` wiring and nginx block
live in `cloud-management/gcp/compute` (`stacks/curate.yml`, `bin/deploy.sh`,
`services/nginx/nginx.conf`).

## First deploy (Nathan's checklist)

Run from `cloud-management/gcp/compute`, with `GOAT=goat.us-east4-c.nsheff`.
Prerequisites: the repo and `cloud-management` changes are committed, and plans
1 to 4 have landed. After Phase 3 step 7 the migration files freeze (see
"Migrations"), so land any pending edits to existing migration files first.
Stop at any step that does not go as described. Never paste a secret into a
chat.

### Before you start: read-only checks

An agent skipped these because they need ssh to goat. Run them and look at the
answers; none of them changes anything.

```bash
# Goat capacity. The app adds about 512M (API) + 512M (worker) + up to 2G (runner cap).
# If /var has under 5 GB free, or used memory plus the new caps passes physical RAM, stop and decide.
ssh $GOAT "free -m; df -h /var; docker system df; docker stats --no-stream --format '{{.Name}} {{.MemUsage}}'"

# Nothing named curate exists yet (each should print nothing or be missing).
ls ~/.password-store/webdev/curate/
ssh $GOAT "docker secret ls | grep curate; docker service ls | grep curate"
ssh $GOAT "docker exec \$(docker ps -q -f name=goat_postgres) psql -U postgres -tAc \"select 1 from pg_database where datname='curate'\""
source ../../cloudflare/auth/databio-org.env
../../cloudflare/dns/bin/update-dns.sh get curate.databio.org     # expect no record
../../cloudflare/dns/bin/update-dns.sh get pigeon.databio.org     # note its type and content; the new record copies it
ssh $GOAT 'cid=$(docker ps -q -f name=prosopia_prosopia\. | head -1); docker exec "$cid" sh -c "set -a && . /run/secrets/prosopia_env && set +a && prosopia oauth list-clients"'   # confirm no `curate` client
```

### Phase 3: go-live (every step changes something outside the repo)

1. **Create the `pass` entries** (letters and digits only in the database
   password, to avoid quoting trouble in `provision-db.sh`):
   ```bash
   pass generate -n webdev/curate/db_password 32
   pass generate -n webdev/curate/jwt_secret 48
   pass generate -n webdev/curate/session_secret 48
   pass git push
   ```
2. **Provision the database and role.** `./bin/provision-db.sh curate` creates
   role `curate` (password from `webdev/curate/db_password`) and database
   `curate` owned by it in `goat_postgres`. It is not idempotent: a rerun
   errors on CREATE, which only means it already ran. Check:
   `ssh $GOAT "docker exec \$(docker ps -q -f name=goat_postgres) psql -U postgres -tAc \"select datname, pg_get_userbyid(datdba) from pg_database where datname='curate'\""`
   gives `curate|curate`. The role owns its database, so `cwb db migrate`
   creates every table; nothing else is granted.
3. **Register the Prosopia OIDC client**, in your own terminal so the secret
   never passes through a chat or agent transcript:
   ```bash
   ssh $GOAT 'cid=$(docker ps -q -f name=prosopia_prosopia\. | head -1); docker exec "$cid" sh -c "set -a && . /run/secrets/prosopia_env && set +a && prosopia oauth add-client curate --name \"Curation Workbench\" --redirect-uri https://curate.databio.org/auth/login/callback --redirect-uri http://127.0.0.1:8134/auth/login/callback"'
   pass insert webdev/curate/oidc_client_secret
   ```
   Paste the secret the first command prints into `pass insert`. Keep
   Prosopia's default (require a verified email): invite-only approval and the
   superadmin match are keyed on email. Check without printing:
   `test -n "$(pass show webdev/curate/oidc_client_secret)" && echo ok`, and
   re-run `prosopia oauth list-clients` to see `curate`.
4. **Push the Swarm secret.** `./bin/deploy.sh --push-secrets curate`. It
   evaluates `production.env` locally, fails before any remote change if a
   required value is empty, and creates `curate_env` on goat. Check:
   `ssh $GOAT "docker secret ls | grep curate_env"`.
5. **Deploy the nginx config.** `./bin/deploy.sh nginx` (runs
   `test-nginx.sh --syntax-only`, copies `services/nginx`, reloads
   `goat_nginx`). Check another site still answers:
   `curl -sS -o /dev/null -w '%{http_code}\n' https://pigeon.databio.org/health`.
6. **Create the DNS record.**
   `source ../../cloudflare/auth/databio-org.env && ../../cloudflare/dns/bin/update-dns.sh set curate.databio.org <content from the pigeon lookup above> --proxied`
   (add `--type CNAME` if Pigeon's is a CNAME). It must be proxied: goat
   accepts only Cloudflare. Check `dig +short curate.databio.org` returns
   Cloudflare addresses, and
   `curl -sS -o /dev/null -w '%{http_code}\n' https://curate.databio.org/health`
   gives 502 (nginx reached, app not up yet), not 522 or 526 (see
   `cloud-management/cloudflare/dns/docs/cloudflare-dns.md` for the "Full
   (strict)" gotcha).
7. **First deploy.** `./bin/deploy.sh curate`. It builds and pushes
   `curate:latest`, deploys stack `curate`, waits for `curate_curate` to be
   healthy and the worker and runner to be 1/1, and runs `verify.sh`. Every
   check must PASS (the resident check SKIPs until you register a project). If
   the rollout times out on the first pull, rerun
   `ROLLOUT_TIMEOUT=1200 ./bin/deploy.sh curate --no-build`. If a check fails,
   collect
   `ssh $GOAT "docker service ps --no-trunc curate_curate curate_curate-worker curate_curate-runner; docker service logs --tail 100 curate_curate; docker service logs --tail 100 curate_curate-worker"`
   and do not try fixes on goat by hand.
8. **Record.** `jot log --project curation-workbench "..."` with the verify
   result and the worker and runner memory
   (`ssh $GOAT 'docker stats --no-stream --format "{{.Name}} {{.MemUsage}}"' | grep curate`).

### Phase 4: sign-in and bootstrap

1. **Sign in** at `https://curate.databio.org` with "Sign in with Prosopia".
   Success: you land back signed in, and the admin entry appears (superadmin
   through `SNAPGATE_SUPERADMIN_EMAILS`, matched on the verified email
   `nsheff@databio.org`). If Prosopia shows a redirect mismatch, the client's
   redirect URI and `OIDC_REDIRECT_URI` differ; compare them. If the app
   redirects to an error page: `docker service logs --since 10m curate_curate`.
2. **Confirm one user (read-only).** In the API task, count `snapgate_users`
   rows (print a count only, never emails). Expect 1, status approved.
3. **Register ONGA as the first hosted project.** ONGA's adapter must already
   be in the ONGA repo and pushed (plan 6). In the API task with `curate_env`
   sourced (see "Running a command in a task"):
   ```bash
   cwb db project add onga --name ONGA
   cwb hosted resident add --project onga --repo https://github.com/databio/onga \
       --branch master --config workbench.yaml --plugin-dir curation/plugin
   cwb hosted resident plugin-diff onga        # read the plugin code before approving
   cwb hosted resident approve-plugin onga --tree <tree sha printed by plugin-diff>
   cwb hosted resident sync onga --wait
   cwb hosted resident show onga
   cwb hosted admin add-member --project onga --email nsheff@databio.org --role curator
   ```
4. **Watch the first sync.** The worker log (`docker logs` of the current
   `curate_curate-worker` container) shows a fetch of `databio/onga` and a
   snapshot published; `resident show` reports a current snapshot at ONGA's
   head and no `last_error`. The ONGA review queue appears in the app. Record
   the worker's and runner's peak memory during this sync. Local numbers for
   ONGA were API 121 MiB, worker 165 MiB, runner 87 MiB; if goat is well under
   the caps, lowering one is another `deploy.sh curate --no-build`.
5. **Optional webhook.** In GitHub `databio/onga` settings, add a push webhook
   to `https://curate.databio.org/api/v1/hooks/github/onga`, content type
   JSON, secret = the one `resident add` printed. Polling (300 s) works
   without it.
6. **Invite flow check (optional).** Ask one colleague to sign in. The account
   shows as pending at `/auth/admin/users`; approve it and add the person to
   the ONGA team. Nobody else may run the approve step.
7. **Docs and log.** Mark the first deploy done below, state the frozen
   migrations rule as current, and run
   `jot log --project curation-workbench "curate.databio.org live on goat; ..."`.

First deploy done: not yet.

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
requests (plus one unsigned webhook POST that must be refused) and read-only
commands.

## Running a command in a task

The CLI needs the secret's environment, so source it inside the container:

```bash
ssh $GOAT
cid=$(docker ps -q -f label=com.docker.swarm.service.name=curate_curate -f health=healthy | head -1)
docker exec -it "$cid" sh -c 'set -a && . /run/secrets/curate_env && set +a && cwb hosted resident list'
```

## First admin and accounts

Nathan is the superadmin through `SNAPGATE_SUPERADMIN_EMAILS`. New sign-ups are
pending; approve them at `/auth/admin/users` (the app's accounts page), then add
them to a project's team (`cwb hosted admin add-member`).

## Registering a project

`cwb db project add`, `cwb hosted resident add`, `plugin-diff`, `approve-plugin`,
`sync --wait` (see Phase 4 step 3 and `dev-docs/sync.md`). Only a superadmin can
do this.

## Plugin safety

Project plugin code runs only in `curate-runner`, only from a tree a superadmin
approved with `cwb hosted resident approve-plugin`. When a commit changes the
plugin code, the worker keeps running the last approved tree until someone reads
the diff (`plugin-diff`) and approves the new one. The worker and API never
import plugin code. Proof it holds on goat: `cwb hosted isolation-check` in the
worker task ends `ISOLATED` (`verify.sh` runs it).

## Rotating secrets

Change the value in `pass`, then `bin/deploy.sh --push-secrets curate`
(restarts the API and worker, which mount `curate_env`).

## Logs

```bash
docker service logs -f --tail 200 curate_curate
docker service logs -f --tail 200 curate_curate-worker     # one line per job; never a secret
docker service logs -f --tail 200 curate_curate-runner
docker service ps curate_curate curate_curate-worker curate_curate-runner
```

## Migrations

`cwb db migrate` runs only in the API service, before `cwb hosted serve`, under
a Postgres advisory lock. The worker never migrates; the runner has no database
access.

- **Before the first production deploy** (now): files in `deployment/migrations/`
  may be edited freely, and a local dev database dropped and recreated.
- **After the first deploy** (Phase 3 step 7): applied migration files are
  frozen. Schema changes only add new numbered files. Update this section when
  the first deploy is done.
- **If a pre-launch reset is needed** after a test deploy, do not write a
  compatibility migration: drop and recreate the database
  (`DROP DATABASE curate; CREATE DATABASE curate OWNER curate;` in
  `goat_postgres`) and redeploy.

## Local image test

```bash
docker build -t curate:dev -f deployment/Dockerfile .
```

Run the API as `sh -c 'cwb db migrate && exec cwb hosted serve --host 0.0.0.0 --port 8134'`,
the runner as `cwb runner --port 8150` (`--read-only --tmpfs /tmp --cap-drop ALL`,
internal network only) and the worker as `cwb hosted worker` with
`CWB_RUNNER_URL=http://<runner>:8150`, all against a throwaway Postgres.
