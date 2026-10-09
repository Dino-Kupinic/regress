#!/usr/bin/env bash
set -euo pipefail

: "${REGRESS_PUBLIC_ORIGIN:?Set REGRESS_PUBLIC_ORIGIN to the app HTTPS origin}"

if [[ ! "$REGRESS_PUBLIC_ORIGIN" =~ ^https://[^/[:space:]]+$ ]]; then
    echo 'REGRESS_PUBLIC_ORIGIN must be an HTTPS origin without a path or trailing slash.' >&2
    exit 1
fi

umask 077
mkdir -p /data/project /data/config /data/cache
if [[ ! -f /data/project/package.json ]]; then
    cp -a /seed/examples/. /data/project/
fi
chown -R regress:regress /data

setpriv --no-new-privs gosu regress \
    /app/.venv/bin/regress serve /data/project --host 127.0.0.1 --origin "$REGRESS_PUBLIC_ORIGIN" &
api_pid=$!
nginx -g 'daemon off;' &
nginx_pid=$!

stop() {
    kill -TERM "$api_pid" "$nginx_pid" 2>/dev/null || true
    for ((attempt=0; attempt<25; attempt++)); do
        if ! kill -0 "$api_pid" 2>/dev/null && ! kill -0 "$nginx_pid" 2>/dev/null; then
            break
        fi
        sleep 1
    done
    kill -KILL "$api_pid" "$nginx_pid" 2>/dev/null || true
    wait "$api_pid" "$nginx_pid" 2>/dev/null || true
}
trap stop EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

set +e
wait -n "$api_pid" "$nginx_pid"
status=$?
set -e
# Either service exiting unexpectedly should trigger the platform's restart policy.
if [[ "$status" == 0 ]]; then status=1; fi
exit "$status"
