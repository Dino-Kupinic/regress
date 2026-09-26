#!/usr/bin/env bash
set -euo pipefail

: "${REGRESS_PUBLIC_ORIGIN:?Set REGRESS_PUBLIC_ORIGIN to the app HTTPS origin}"

mkdir -p /data/project
if [[ ! -f /data/project/package.json ]]; then
    cp -a /seed/examples/. /data/project/
fi

/app/.venv/bin/regress serve /data/project --host 127.0.0.1 --origin "$REGRESS_PUBLIC_ORIGIN" &
api_pid=$!
nginx -g 'daemon off;' &
nginx_pid=$!

stop() {
    kill -TERM "$api_pid" "$nginx_pid" 2>/dev/null || true
    wait "$api_pid" "$nginx_pid" 2>/dev/null || true
}
trap stop EXIT TERM INT

set +e
wait -n "$api_pid" "$nginx_pid"
status=$?
set -e
exit "$status"
