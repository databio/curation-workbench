#!/usr/bin/env bash
# Local dev Postgres for the hosted store: scripts/dev-db.sh start|stop|url
# Container cwb-dev-pg, named volume cwb-dev-pg, 127.0.0.1:${CWB_DEV_PG_PORT:-55436}.
#
# Recreate it from scratch (before the first production deploy, migration
# mistakes are fixed this way):
#   docker rm -f cwb-dev-pg && docker volume rm cwb-dev-pg && scripts/dev-db.sh start
set -euo pipefail

NAME="cwb-dev-pg"
PORT="${CWB_DEV_PG_PORT:-55436}"
URL="postgresql+psycopg://cwb:cwb@127.0.0.1:$PORT/cwb"

wait_ready() {
    for _ in $(seq 1 60); do
        if docker exec "$NAME" psql -U cwb -d cwb -c 'SELECT 1' >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "postgres in $NAME did not become ready" >&2
    return 1
}

case "${1:-}" in
    start)
        if docker container inspect "$NAME" >/dev/null 2>&1; then
            docker start "$NAME" >/dev/null
        else
            docker run -d --name "$NAME" -v cwb-dev-pg:/var/lib/postgresql/data \
                -p "127.0.0.1:$PORT:5432" \
                -e POSTGRES_USER=cwb -e POSTGRES_PASSWORD=cwb -e POSTGRES_DB=cwb \
                postgres:16 >/dev/null
        fi
        wait_ready
        echo "$URL"
        ;;
    stop)
        docker stop "$NAME" >/dev/null
        ;;
    url)
        echo "$URL"
        ;;
    *)
        echo "usage: $0 start|stop|url" >&2
        exit 2
        ;;
esac
