#!/usr/bin/env bash
# verify.sh [base-url]
#
# Read-only acceptance check for a DEPLOYED curation workbench. Checks are hard
# pass/fail and the script exits nonzero if any fails. It creates, edits and
# deletes nothing: it only sends GET requests (plus one unsigned webhook POST
# that the app must reject), reads Swarm state and logs, and runs the read-only
# `cwb db status`, `cwb hosted resident list` and `cwb hosted isolation-check`.
#
# cloud-management/gcp/compute/bin/deploy.sh curate runs it after the Swarm
# rollout converges; it also runs the same way by hand:
#
#   deployment/verify.sh https://curate.databio.org
#
# Environment:
#   CWB_VERIFY_SSH_HOST       ssh target running the Swarm tasks
#                             (default: goat.us-east4-c.nsheff, as deploy.sh)
#   CWB_VERIFY_SERVICE        API service (default: curate_curate)
#   CWB_VERIFY_WORKER_SERVICE worker service (default: curate_curate-worker)
#   CWB_VERIFY_RUNNER_SERVICE runner service (default: curate_curate-runner)
#   CWB_VERIFY_RUN_NET        the runner's only network (default: curate_run_net)
#   CWB_VERIFY_CLIENT_ID      the Prosopia client id (default: curate)
#   CWB_VERIFY_WAIT           seconds to wait for /health after a stop-first
#                             replacement (default: 180)
#   CWB_VERIFY_RETRIES        attempts per check before it FAILs (default: 3)
#   CWB_VERIFY_SKIP_SSH=1     skip the checks that need ssh to goat (hand runs,
#                             local dry runs). Reported as SKIP, never PASS.

# The check functions run indirectly, through `check`.
# shellcheck disable=SC2329

set -uo pipefail

BASE="${1:-https://curate.databio.org}"
BASE="${BASE%/}"
SSH_HOST="${CWB_VERIFY_SSH_HOST:-goat.us-east4-c.nsheff}"
SERVICE="${CWB_VERIFY_SERVICE:-curate_curate}"
WORKER_SERVICE="${CWB_VERIFY_WORKER_SERVICE:-curate_curate-worker}"
RUNNER_SERVICE="${CWB_VERIFY_RUNNER_SERVICE:-curate_curate-runner}"
RUN_NET="${CWB_VERIFY_RUN_NET:-curate_run_net}"
CLIENT_ID="${CWB_VERIFY_CLIENT_ID:-curate}"
WAIT="${CWB_VERIFY_WAIT:-180}"
RETRIES="${CWB_VERIFY_RETRIES:-3}"
SKIP_SSH="${CWB_VERIFY_SKIP_SSH:-}"

# The line the worker logs when it enters its claim loop
# (STARTED in src/curation_workbench/hosted/sync/worker.py).
WORKER_LOOP_LINE="cwb worker started"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PASS=0
FAIL=0
SKIP=0

# fetch <url> [curl args...] -> body in $WORK/body, headers in $WORK/headers; prints the status.
fetch() {
  local url="$1"; shift
  curl -s -o "$WORK/body" -D "$WORK/headers" -w '%{http_code}' --max-time 20 "$@" "$url" 2>/dev/null || printf '000'
}

# header <name> -> the value of the last such header in $WORK/headers (no CR).
header() {
  tr -d '\r' < "$WORK/headers" | grep -i "^$1:" | tail -n1 | sed 's/^[^:]*:[[:space:]]*//'
}

# json_check <python expression over `d`> (reads $WORK/body)
json_check() {
  python3 - "$1" "$WORK/body" <<'PY'
import json, sys
expr, path = sys.argv[1], sys.argv[2]
try:
    d = json.load(open(path))
except Exception:
    sys.exit(1)
sys.exit(0 if eval(expr, {"d": d}) else 1)
PY
}

# check <label> <function...>: retry a few times (the stop-first window), then
# record a hard PASS/FAIL.
check() {
  local label="$1"; shift
  local i
  for ((i = 1; i <= RETRIES; i++)); do
    if "$@"; then
      echo "  PASS  $label"; PASS=$((PASS + 1)); return 0
    fi
    [ "$i" -lt "$RETRIES" ] && sleep 5
  done
  echo "  FAIL  $label"; FAIL=$((FAIL + 1)); return 1
}

skip() {
  echo "  SKIP  $1"; SKIP=$((SKIP + 1))
}

on_goat() {
  ssh -o BatchMode=yes -o ConnectTimeout=15 "$SSH_HOST" "$@"
}

# The id of a running container of a Swarm service on the node (empty if none).
CID_CMD='docker ps -q -f label=com.docker.swarm.service.name='

# --- individual checks -------------------------------------------------------

# 1. Service, database and schema are healthy.
health_ok() {
  [ "$(fetch "$BASE/health")" = "200" ] \
    && json_check 'd.get("status") == "ok" and d.get("checks", {}).get("db") == "ok" and d.get("checks", {}).get("schema") == "ok"'
}

# 2. The request went through Cloudflare.
health_via_cloudflare() {
  fetch "$BASE/health" >/dev/null
  [ -n "$(header cf-ray)" ]
}

# 3. The SPA shell (frontend/index.html has <div id="root">).
spa_root_html() {
  [ "$(fetch "$BASE/")" = "200" ] \
    && header content-type | grep -qi '^text/html' \
    && grep -q 'id="root"' "$WORK/body"
}

# 4. snapgate's /auth/me never 401s: anonymous gets 200 {"user": null}
#    (src/snapgate/core.py get_current_user_info).
auth_me_anonymous() {
  [ "$(fetch "$BASE/auth/me")" = "200" ] && json_check 'd.get("user") is None'
}

# 5. /auth/login redirects to Prosopia's authorize page with exactly our
#    callback and our client id.
oidc_login_redirect() {
  local location
  fetch "$BASE/auth/login" >/dev/null
  head -n1 "$WORK/headers" | grep -qE '^HTTP/[0-9.]+ 30[1237]' || return 1
  location="$(header location)"
  python3 - "$location" "$BASE/auth/login/callback" "$CLIENT_ID" <<'PY'
import sys
from urllib.parse import parse_qs, urlsplit
location, callback, client_id = sys.argv[1:4]
u = urlsplit(location)
q = parse_qs(u.query)
if u.scheme != "https" or u.hostname != "prosopia.databio.org" or u.path != "/oauth/authorize":
    print(f"        redirect goes to {u.hostname or '(none)'}")
    sys.exit(1)
if q.get("redirect_uri") != [callback]:
    print(f"        redirect_uri is {q.get('redirect_uri')}, want {callback}")
    sys.exit(1)
if q.get("client_id") != [client_id]:
    print(f"        client_id is {q.get('client_id')}, want {client_id}")
    sys.exit(1)
PY
}

# 6. The API needs a sign-in.
projects_need_signin() {
  [ "$(fetch "$BASE/api/v1/projects")" = "401" ]
}

# 7. The mode switch says hosted, and demo login does not exist.
config_hosted() {
  [ "$(fetch "$BASE/api/config")" = "200" ] && json_check 'd.get("mode") == "hosted"'
}

demo_login_absent() {
  [ "$(fetch "$BASE/auth/demo-login/user")" = "404" ]
}

# 8. A browser deep link gets the SPA shell; an unknown API path stays a 404.
spa_deep_link() {
  [ "$(fetch "$BASE/p/x" -H 'Accept: text/html')" = "200" ] && grep -q 'id="root"' "$WORK/body"
}

api_bogus_404() {
  [ "$(fetch "$BASE/api/bogus" -H 'Accept: text/html')" = "404" ] \
    && header content-type | grep -qi 'json'
}

# 9. An unsigned webhook is refused (401 bad signature, or 404 no such webhook).
#    The only POST here, and it changes nothing.
webhook_unsigned() {
  local code
  code="$(fetch "$BASE/api/v1/hooks/github/verify-probe" -X POST -H 'Content-Type: application/json' -d '{}')"
  [ "$code" = "401" ] || [ "$code" = "404" ]
}

# 10. (ssh) The image's migrations and the live database agree (read-only), run
#     in the API task with the mounted secret sourced inside the container.
db_status_in_task() {
  local out rc
  out="$(on_goat \
    "cid=\$(${CID_CMD}${SERVICE} -f health=healthy | head -n1); \
     [ -n \"\$cid\" ] || { echo 'no healthy ${SERVICE} task'; exit 1; }; \
     docker exec \"\$cid\" sh -c 'set -a && . /run/secrets/curate_env && set +a && cwb db status'" 2>&1)"
  rc=$?
  printf '%s\n' "$out" | sed 's/^/        /'
  return "$rc"
}

# 11. (ssh) One worker replica, and it reached its claim loop.
worker_running() {
  local replicas
  replicas="$(on_goat "docker service ls --filter name=${WORKER_SERVICE} --format '{{.Name}} {{.Replicas}}'" 2>/dev/null \
    | awk -v s="$WORKER_SERVICE" '$1 == s {print $2}')"
  [ "$replicas" = "1/1" ] || { echo "        replicas: ${replicas:-(service not found)}"; return 1; }
  # The running container's own log: `docker service logs` interleaves old and
  # new tasks, so its last start line can belong to the replaced worker.
  on_goat "cid=\$(${CID_CMD}${WORKER_SERVICE} | head -n1); \
    [ -n \"\$cid\" ] && docker logs --tail 500 \"\$cid\" 2>&1" > "$WORK/worker.log" 2>/dev/null || return 1
  grep -q "^${WORKER_LOOP_LINE}" "$WORK/worker.log"
}

# 12. (ssh) The worker never migrates: no `migrate` in its entrypoint, and no
#     "applied N migration(s)" line in its log (needs check 11's log).
worker_never_migrates() {
  local entry
  entry="$(on_goat "docker service inspect ${WORKER_SERVICE} --format '{{json .Spec.TaskTemplate.ContainerSpec.Command}} {{json .Spec.TaskTemplate.ContainerSpec.Args}}'" 2>/dev/null)" || return 1
  case "$entry" in *migrate*) echo "        entrypoint mentions migrate: $entry"; return 1 ;; esac
  [ -s "$WORK/worker.log" ] || return 1
  if grep -qi 'applied [0-9]* migration' "$WORK/worker.log"; then
    echo "        the worker log has a migration-applied line"; return 1
  fi
}

# 13. (ssh) The worker publishes no port and has no databio_net alias.
worker_no_ports_no_alias() {
  local out
  local own="${WORKER_SERVICE#*_}" alias
  out="$(on_goat "docker service inspect ${WORKER_SERVICE} --format 'ports={{len .Endpoint.Ports}} {{range .Spec.TaskTemplate.Networks}}{{range .Aliases}}alias={{.}} {{end}}{{end}}'" 2>/dev/null)" || return 1
  echo "        $out"
  case "$out" in *"ports=0 "*) ;; *) return 1 ;; esac
  # docker stack deploy always adds the service's own short name; any other
  # alias (such as the API's name) is a failure.
  for alias in $(printf '%s' "$out" | grep -oE 'alias=[^ ]+'); do
    [ "${alias#alias=}" = "$own" ] || return 1
  done
  return 0
}

# 14. (ssh) The isolated runner: 1/1, no secrets, no env from curate_env,
#     read-only root, no capabilities, no published port, and attached only to
#     the internal run network.
runner_isolated() {
  local replicas spec nets bad=0
  replicas="$(on_goat "docker service ls --filter name=${RUNNER_SERVICE} --format '{{.Name}} {{.Replicas}}'" 2>/dev/null \
    | awk -v s="$RUNNER_SERVICE" '$1 == s {print $2}')"
  [ "$replicas" = "1/1" ] || { echo "        replicas: ${replicas:-(service not found)}"; return 1; }
  spec="$(on_goat "docker service inspect ${RUNNER_SERVICE} --format 'secrets={{len .Spec.TaskTemplate.ContainerSpec.Secrets}} readonly={{.Spec.TaskTemplate.ContainerSpec.ReadOnly}} capdrop={{json .Spec.TaskTemplate.ContainerSpec.CapabilityDrop}} ports={{len .Endpoint.Ports}} env={{json .Spec.TaskTemplate.ContainerSpec.Env}}'" 2>/dev/null)" || return 1
  echo "        ${spec%% env=*}"
  case "$spec" in *"secrets=0 "*) ;; *) echo "        the runner has secrets mounted"; bad=1 ;; esac
  case "$spec" in *"readonly=true "*) ;; *) echo "        the runner's root is not read-only"; bad=1 ;; esac
  case "$spec" in *'capdrop=["ALL"]'*) ;; *) echo "        the runner does not drop all capabilities"; bad=1 ;; esac
  case "$spec" in *"ports=0 "*) ;; *) echo "        the runner publishes a port"; bad=1 ;; esac
  if printf '%s' "$spec" | grep -qE 'POSTGRES_|JWT_|SESSION_SECRET|OIDC_|curate_env'; then
    echo "        the runner's environment looks like it holds secrets"; bad=1
  fi
  nets="$(on_goat "for n in \$(docker service inspect ${RUNNER_SERVICE} --format '{{range .Spec.TaskTemplate.Networks}}{{.Target}} {{end}}'); do docker network inspect -f '{{.Name}}:{{.Internal}}' \"\$n\"; done" 2>/dev/null | tr '\n' ' ' | sed 's/ $//')"
  echo "        networks: ${nets:-(none)}"
  [ "$nets" = "${RUN_NET}:true" ] || { echo "        want only ${RUN_NET} (internal)"; bad=1; }
  [ "$bad" = 0 ]
}

# 15. (ssh) The live proof of isolation: `cwb hosted isolation-check` in the
#     worker task (a probe plugin tries to read the secret, reach the internet
#     and write outside /tmp) ends ISOLATED.
isolation_check_in_task() {
  local out
  out="$(on_goat \
    "cid=\$(${CID_CMD}${WORKER_SERVICE} | head -n1); \
     [ -n \"\$cid\" ] || { echo 'no ${WORKER_SERVICE} task'; exit 1; }; \
     docker exec \"\$cid\" sh -c 'set -a && . /run/secrets/curate_env && set +a && cwb hosted isolation-check'" 2>&1)"
  printf '%s\n' "$out" | sed 's/^/        /'
  [ "$(printf '%s\n' "$out" | tail -n1)" = "ISOLATED" ]
}

# 16. (ssh) Read the resident list in the API task (read-only) into
#     $WORK/residents.
read_residents() {
  local out
  out="$(on_goat \
    "cid=\$(${CID_CMD}${SERVICE} -f health=healthy | head -n1); \
     [ -n \"\$cid\" ] || { echo 'no healthy ${SERVICE} task'; exit 1; }; \
     docker exec \"\$cid\" sh -c 'set -a && . /run/secrets/curate_env && set +a && cwb hosted resident list 2>/dev/null'" 2>&1)" || { printf '%s\n' "$out" | sed 's/^/        /'; return 1; }
  printf '%s\n' "$out" > "$WORK/residents"
}

# Every enabled resident has a current snapshot and no last_error.
residents_current() {
  sed 's/^/        /' "$WORK/residents"
  ! grep ' enabled ' "$WORK/residents" | grep -qv 'snapshot=[0-9]' \
    && ! grep ' enabled ' "$WORK/residents" | grep -qv 'error=-$'
}

# --- run ---------------------------------------------------------------------

echo "Verifying $BASE"

# Wait out the stop-first replacement window before judging anything.
deadline=$(( $(date +%s) + WAIT ))
until health_ok; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "  (still not healthy after ${WAIT}s; continuing so every check reports)"
    break
  fi
  sleep 5
done

echo "=== Service ==="
check "/health is 200 with status=ok, checks.db=ok, checks.schema=ok" health_ok
check "/health response carries cf-ray (served through Cloudflare)" health_via_cloudflare
check "GET /api/config says mode=hosted" config_hosted
check "GET / is 200 text/html with the SPA root element" spa_root_html
check "GET /p/x with Accept: text/html gets the SPA shell" spa_deep_link
check "GET /api/bogus is a JSON 404, not the SPA shell" api_bogus_404

echo "=== Sign-in surface ==="
check "GET /auth/me without credentials is 200 {user: null}" auth_me_anonymous
check "GET /auth/login redirects to prosopia.databio.org/oauth/authorize with redirect_uri $BASE/auth/login/callback and client_id $CLIENT_ID" oidc_login_redirect
check "GET /api/v1/projects without credentials is 401" projects_need_signin
check "GET /auth/demo-login/user is 404 (demo login is off)" demo_login_absent
check "POST /api/v1/hooks/github/<slug> without a signature is 401 or 404" webhook_unsigned

echo "=== Inside goat (ssh $SSH_HOST) ==="
if [ "$SKIP_SSH" = "1" ]; then
  skip "cwb db status in $SERVICE (CWB_VERIFY_SKIP_SSH=1)"
  skip "$WORKER_SERVICE is 1/1 and logged '$WORKER_LOOP_LINE' (CWB_VERIFY_SKIP_SSH=1)"
  skip "$WORKER_SERVICE never migrates (CWB_VERIFY_SKIP_SSH=1)"
  skip "$WORKER_SERVICE publishes no port and no alias but its own name (CWB_VERIFY_SKIP_SSH=1)"
  skip "$RUNNER_SERVICE is 1/1 and isolated (CWB_VERIFY_SKIP_SSH=1)"
  skip "cwb hosted isolation-check in $WORKER_SERVICE ends ISOLATED (CWB_VERIFY_SKIP_SSH=1)"
  skip "residents have a current snapshot and no error (CWB_VERIFY_SKIP_SSH=1)"
else
  check "cwb db status exits 0 in $SERVICE" db_status_in_task
  check "$WORKER_SERVICE is 1/1 and logged '$WORKER_LOOP_LINE'" worker_running
  check "$WORKER_SERVICE never migrates (no migrate in its entrypoint or log)" worker_never_migrates
  check "$WORKER_SERVICE publishes no port and no alias but its own name" worker_no_ports_no_alias
  check "$RUNNER_SERVICE is 1/1, has no secrets, a read-only root, no capabilities, no port, and only $RUN_NET" runner_isolated
  check "cwb hosted isolation-check in $WORKER_SERVICE ends ISOLATED" isolation_check_in_task
  if ! read_residents; then
    echo "  FAIL  cwb hosted resident list runs in $SERVICE"; FAIL=$((FAIL + 1))
  elif grep -q '^no residents' "$WORK/residents"; then
    skip "residents have a current snapshot and no last_error (no residents registered yet)"
  else
    check "every enabled resident has a current snapshot and no last_error" residents_current
  fi
fi

echo ""
echo "Passed: $PASS  Failed: $FAIL  Skipped: $SKIP"
if [ "$FAIL" -gt 0 ]; then
  echo "VERIFICATION FAILED"
  exit 1
fi
echo "ALL CHECKS PASSED"
exit 0
