# Coolify deployment

The root `Dockerfile` builds the React Router SPA and Python API into one application. Nginx serves the frontend on port 8080 and proxies `/api/` to `regress serve`. Coolify provides HTTPS and HTTP Basic Authentication.

Create a Dockerfile application from this repository with `Dockerfile` as its build file, port `8080`, and a domain such as `https://regress.dino-kupinic.dev`. Enable Coolify's HTTP Basic Authentication before deploying. Set these runtime variables:

```text
REGRESS_PUBLIC_ORIGIN=https://regress.dino-kupinic.dev
OPENAI_API_KEY=<your key>
```

Add a persistent volume mounted at `/data`. The entrypoint copies the bundled example project into `/data/project` on first start, including its installed Vitest and Stryker dependencies. Tests, generated files, and `.regress` reports then survive application redeploys. To reset the example project, remove the volume deliberately from Coolify.

This deployment serves one example project and runs one job at a time. Keep it to one application instance because job coordination is in process and the project files are mutable. To analyze another repository, populate `/data/project` with that JavaScript or TypeScript project and install its Vitest and Stryker dependencies there.
