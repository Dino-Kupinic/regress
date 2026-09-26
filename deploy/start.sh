#!/usr/bin/env bash
set -euo pipefail

: "${REGRESS_PUBLIC_ORIGIN:?Set REGRESS_PUBLIC_ORIGIN to the app HTTPS origin}"
: "${REGRESS_BASIC_AUTH_USER:?Set REGRESS_BASIC_AUTH_USER}"
: "${REGRESS_BASIC_AUTH_PASSWORD:?Set REGRESS_BASIC_AUTH_PASSWORD}"

umask 077
password_hash="$(printf '%s' "$REGRESS_BASIC_AUTH_PASSWORD" | openssl passwd -apr1 -stdin)"
printf '%s:%s\n' "$REGRESS_BASIC_AUTH_USER" "$password_hash" > /etc/nginx/.htpasswd
chgrp www-data /etc/nginx/.htpasswd
chmod 640 /etc/nginx/.htpasswd

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
