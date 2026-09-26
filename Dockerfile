FROM node:22-bookworm-slim AS web-build
COPY --from=oven/bun:1.3.13 /usr/local/bin/bun /usr/local/bin/bun
WORKDIR /build/web
COPY web/package.json web/bun.lock web/.npmrc ./
RUN bun install --frozen-lockfile
COPY web/ ./
RUN npm run build

FROM oven/bun:1.3.13 AS examples-build
WORKDIR /build/examples
COPY examples/package.json examples/bun.lock ./
RUN bun install --frozen-lockfile

FROM python:3.13-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl nginx libstdc++6 openssl util-linux tini gosu procps \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --system --create-home --home-dir /home/regress regress
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=oven/bun:1.3.13 /usr/local/bin/bun /usr/local/bin/bun
COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /uvx /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev

COPY --from=web-build /build/web/build/client /app/web/build/client
COPY examples/ /seed/examples/
COPY --from=examples-build /build/examples/node_modules /seed/examples/node_modules
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/start.sh /app/start.sh
RUN chmod +x /app/start.sh \
    && rm -f /etc/nginx/sites-enabled/default

ENV PYTHONUNBUFFERED=1
ENV XDG_CONFIG_HOME=/data/config XDG_CACHE_HOME=/data/cache
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:8080/healthz > /dev/null || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/start.sh"]
