# Regress web app

The React Router SPA uses the Python API on `127.0.0.1:8765`. Vite proxies `/api`, so browser requests stay on the local app origin. Provider API keys are read only by Python. `regress serve` needs Linux or macOS.

Use Node.js 22 and Bun 1.3.13, the versions CI and the production image pin. The walkthrough is [Using the web app](../docs/content/docs/using/web-app.mdx).

```bash
# Terminal 1, from the repository root
uv run regress serve examples

# Terminal 2
cd web
bun install --frozen-lockfile
bun run dev
```

Open `http://127.0.0.1:5173`. Vite binds that address in `web/vite.config.ts` and proxies `/api` to port 8765. To check the frontend, run `bun run typecheck`, `bun run check`, `bun run test`, and `bun run build` in `web/`. `bun run test` is Vitest. The production build is a static SPA in `web/build/client/`.

Routes: dashboard `/`, runs `/runs`, run detail `/runs/:id`, batch detail `/batches/:id`, evaluation `/evaluation`, settings `/settings`, and project setup `/setup`.

The dashboard and report screens show only data returned by the API. Select one file to open a run detail page, or up to 100 files to open a batch page. Batch files run sequentially; cancelling the batch or its current run stops that run and starts no more files. The drawer discovers test files automatically. Use the CLI or API to specify a test path. Run detail pages poll status and events. The API also offers `GET /api/runs/{id}/events/stream` as server-sent events; this UI does not open that stream. Evaluation results are read from `.regress/eval/`; the Evaluation page can start an oracle or full benchmark through the local API. It evaluates every suite. Module filters and a per-run model are CLI options. Settings save personal defaults through `PATCH /api/settings`. **Refresh models** reloads the cached short list; it does not send `refresh=true`. Project setup calls `POST /api/project/init` and directs the user to set the configured provider's API key in the Python environment when required.
