# Builds the static Fumadocs export and serves it with Nginx on port 8080.
# Build from this directory: docker build --tag regress-docs docs
FROM node:22-bookworm-slim AS build
COPY --from=oven/bun:1.3.13 /usr/local/bin/bun /usr/local/bin/bun
WORKDIR /docs
COPY package.json bun.lock ./
# fumadocs-mdx runs during `next build`, once the content has been copied.
RUN bun install --frozen-lockfile --ignore-scripts
COPY . .
RUN bun run build

FROM nginx:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /docs/out /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --quiet --spider http://127.0.0.1:8080/healthz || exit 1
