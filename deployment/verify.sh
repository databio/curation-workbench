#!/usr/bin/env bash
# verify.sh [base-url]
#
# Read-only acceptance check for a DEPLOYED curation workbench. Checks are hard
# pass/fail and the script exits nonzero if any fails. It creates, edits and
# deletes nothing: it only sends GET requests, reads Swarm state, and runs the
# read-only `cwb db status` and `cwb hosted project list`.
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
#   CWB_VERIFY_STACK          its stack (default: curate)
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
STACK="${CWB_VERIFY_STACK:-curate}"
CLIENT_ID="${CWB_VERIFY_CLIENT_ID:-curate}"
WAIT="${CWB_VERIFY_WAIT:-180}"
RETRIES="${CWB_VERIFY_RETRIES:-3}"
SKIP_SSH="${CWB_VERIFY_SKIP_SSH:-}"

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

# 9. (ssh) The image's migrations and the live database agree (read-only), run
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

# 10. (ssh) The stack is the API alone: the old worker and runner services
#     are gone, and the API task is 1/1.
stack_is_api_only() {
  local out
  out="$(on_goat "docker stack services ${STACK} --format '{{.Name}} {{.Replicas}}'" 2>/dev/null)" || return 1
  printf '%s\n' "$out" | sed 's/^/        /'
  [ "$out" = "${SERVICE} 1/1" ]
}

# 11. (ssh) Read the project list in the API task (read-only) into
#     $WORK/projects: one line per project with a repo,
#     `<slug> <repo>@<branch> commit=<sha> synced=<time> ok|error: ...`.
read_projects() {
  local out
  out="$(on_goat \
    "cid=\$(${CID_CMD}${SERVICE} -f health=healthy | head -n1); \
     [ -n \"\$cid\" ] || { echo 'no healthy ${SERVICE} task'; exit 1; }; \
     docker exec \"\$cid\" sh -c 'set -a && . /run/secrets/curate_env && set +a && cwb hosted project list 2>/dev/null'" 2>&1)" || { printf '%s\n' "$out" | sed 's/^/        /'; return 1; }
  printf '%s\n' "$out" > "$WORK/projects"
}

# Every registered project's data loaded: a snapshot commit and no last error.
projects_loaded() {
  sed 's/^/        /' "$WORK/projects"
  ! grep -qvE ' commit=[0-9a-f]{12} .* ok$' "$WORK/projects"
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

echo "=== Inside goat (ssh $SSH_HOST) ==="
if [ "$SKIP_SSH" = "1" ]; then
  skip "cwb db status in $SERVICE (CWB_VERIFY_SKIP_SSH=1)"
  skip "stack $STACK runs only $SERVICE (CWB_VERIFY_SKIP_SSH=1)"
  skip "registered projects loaded with no error (CWB_VERIFY_SKIP_SSH=1)"
else
  check "cwb db status exits 0 in $SERVICE" db_status_in_task
  check "stack $STACK runs only $SERVICE, at 1/1 (no worker, no runner)" stack_is_api_only
  if ! read_projects; then
    echo "  FAIL  cwb hosted project list runs in $SERVICE"; FAIL=$((FAIL + 1))
  elif grep -q '^no projects with a repo' "$WORK/projects"; then
    skip "registered projects loaded with no error (no project registered yet)"
  else
    check "every registered project has a snapshot commit and no fetch error" projects_loaded
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
