# Coolify deployment

The root `Dockerfile` builds the React Router SPA and Python API into one application. Nginx serves the frontend on port 8080, protects it with HTTP Basic Authentication, and proxies `/api/` to `regress serve`. Coolify provides HTTPS.

Create a Dockerfile application from this repository with `Dockerfile` as its build file, port `8080`, and a domain such as `https://regress.dino-kupinic.dev`. Set these runtime variables:

```text
REGRESS_PUBLIC_ORIGIN=https://regress.dino-kupinic.dev
OPENAI_API_KEY=<your key>
REGRESS_BASIC_AUTH_USER=<a login name>
REGRESS_BASIC_AUTH_PASSWORD=<a strong password>
```

Add a persistent volume mounted at `/data`. The entrypoint copies the bundled example project into `/data/project` on first start, including its installed Vitest and Stryker dependencies. Tests, generated files, and `.regress` reports then survive application redeploys. User settings and model caches live in `/data/config` and `/data/cache`. The entrypoint assigns the volume to the dedicated `regress` account; use a dedicated volume for this application. To reset the example project, remove the volume deliberately from Coolify.

Use `/healthz` for an unauthenticated readiness check. It returns 200 only when the project configuration, toolchain, API key presence and artifact storage are ready; otherwise it returns 503. It does not contact OpenAI or expose project paths. The Docker image includes this health check. `/api/health` remains the authenticated liveness endpoint. All frontend and `/api/` routes require the HTTP Basic Authentication credentials.

This deployment serves one project and runs one job at a time. Keep it to one application instance and one API worker. An exclusive filesystem lock prevents a second API process from opening the same project. Runs, evaluations and dependency installation share one reservation, including during cancellation and cleanup. Stop the old container before starting its replacement against the same volume; rolling overlap on one writable project is unsupported. To analyze another repository, populate `/data/project` with that JavaScript or TypeScript project and install its Vitest and Stryker dependencies there. Do not run CLI jobs against it while the API is serving it.

Set the platform's container stop timeout to **at least 30 seconds** (Docker CLI: `docker stop --time 30`; Compose: `stop_grace_period: 30s`). The entrypoint allows up to 25 seconds to stop both services. Python sends cooperative cancellation first, restores source and test files, and escalates to forced termination for stuck workers. Tini reaps orphaned descendants. The API and generated tests run as the unprivileged `regress` account; Nginx starts as root and uses its normal worker account. Only port 8080 should be reachable by the HTTPS proxy. Port 8765 stays on loopback inside the container.

Before spawning an API run, the server saves a durable recovery record containing the original source and test bytes. If the whole container is killed before completion, the next startup restores those files and marks the run failed. Completed results are preserved. Recovery waits for the prior worker's project lock to be released and refuses invalid recovery records instead of guessing which files to overwrite. Keep the run directory intact when recovering from an interrupted deployment; these records cover new API runs, not older or standalone CLI runs.

Use this instance for repositories and operators you trust: tests execute code with access to the project's files and model credentials. The container is a deployment boundary, not a sandbox between users. Keep the project in version control, back up `/data`, and monitor disk use because saved run and evaluation artifacts are retained until removed. Remove finished runs through the API's `DELETE /api/runs/{id}` endpoint. Set CPU and memory limits in the host according to the size of the project and its Stryker workload.

For failures, inspect the container logs and the run's `.regress/runs/<id>/report.json` and `events.jsonl`. Unexpected HTTP errors include `X-Request-ID`, which identifies the corresponding server log entry. Tool logs retain the last 1 MiB of output; model calls have configurable idle, total-duration and output-token budgets. A 503 readiness response with `project: false` means to check `/api/project`; `storage: false` means to check volume permissions and free space. Stop job submissions before maintenance or restoring a backup.
