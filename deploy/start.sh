#!/usr/bin/env bash
set -euo pipefail

: "${REGRESS_PUBLIC_ORIGIN:?Set REGRESS_PUBLIC_ORIGIN to the app HTTPS origin}"
: "${REGRESS_BASIC_AUTH_USER:?Set REGRESS_BASIC_AUTH_USER}"
: "${REGRESS_BASIC_AUTH_PASSWORD:?Set REGRESS_BASIC_AUTH_PASSWORD}"

if [[ ! "$REGRESS_PUBLIC_ORIGIN" =~ ^https://[^/[:space:]]+$ ]]; then
    echo 'REGRESS_PUBLIC_ORIGIN must be an HTTPS origin without a path or trailing slash.' >&2
    exit 1
fi
if [[ "$REGRESS_BASIC_AUTH_USER" == *:* || "$REGRESS_BASIC_AUTH_USER" == *$'\n'* || "$REGRESS_BASIC_AUTH_USER" == *$'\r'* ]]; then
    echo 'REGRESS_BASIC_AUTH_USER must not contain colons or newlines.' >&2
    exit 1
fi

umask 077
password_hash="$(printf '%s' "$REGRESS_BASIC_AUTH_PASSWORD" | openssl passwd -apr1 -stdin)"
printf '%s:%s\n' "$REGRESS_BASIC_AUTH_USER" "$password_hash" > /etc/nginx/.htpasswd
chgrp www-data /etc/nginx/.htpasswd
chmod 640 /etc/nginx/.htpasswd
unset REGRESS_BASIC_AUTH_PASSWORD REGRESS_BASIC_AUTH_USER

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
