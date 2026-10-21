#!/usr/bin/env bash
# Run the postgres-marked tests against a throwaway postgres:16 container.
#
#   scripts/test-postgres.sh [extra pytest args]
#
# Starts postgres:16 on localhost:55437, runs `pytest -m postgres`, and removes
# the container on exit. Never point CWB_TEST_POSTGRES_URL at goat.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="cwb-test-pg-$$"
PORT="${CWB_TEST_PG_PORT:-55437}"
PYTHON="${PYTHON:-$REPO/.venv/bin/python}"

docker run -d --rm --name "$NAME" -p "127.0.0.1:$PORT:5432" \
    -e POSTGRES_USER=cwb -e POSTGRES_PASSWORD=cwb -e POSTGRES_DB=cwb_test \
    postgres:16 >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

for _ in $(seq 1 60); do
    if docker exec "$NAME" pg_isready -U cwb -d cwb_test >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
# pg_isready can pass during the init restart; wait for a real query.
for _ in $(seq 1 30); do
    if docker exec "$NAME" psql -U cwb -d cwb_test -c 'SELECT 1' >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

export CWB_TEST_POSTGRES_URL="postgresql+psycopg://cwb:cwb@127.0.0.1:$PORT/cwb_test"
cd "$REPO"
"$PYTHON" -m pytest -m postgres "$@"
