# Frontend stack for Regress

The `web/` app is a local browser UI on `127.0.0.1`, written in TypeScript. The Python engine stays separate and holds the API key. The first screen is the dashboard at `/`.

## Recommended stack

| Part | Choice | Why it fits Regress |
| --- | --- | --- |
| UI | [React 19](https://react.dev/) + TypeScript | Components for the dashboard, run status, reports, and reviewing generated tests. Use React and React DOM 19.2.7 or newer for React Router 8. |
| Build and development server | [Vite 8](https://v8.vite.dev/blog/announcing-vite8) | Build and serve the local browser app with the official React Router Framework Mode integration. |
| JavaScript tooling | [Bun](https://bun.sh/docs/pm/cli/install) | Install dependencies and run scripts in `web/`. Commit `web/bun.lock`; keep `uv` for Python. The examples already use Bun. |
| Styling and components | [Tailwind CSS](https://ui.shadcn.com/docs/installation/react-router) + [shadcn/ui](https://ui.shadcn.com/docs/installation/react-router) | A consistent set of cards, tables, dialogs, badges, and progress indicators that lives in the app's source. Use the React Router setup guide for shadcn/ui. |
| Routes and route data | [React Router 8 Framework Mode](https://reactrouter.com/start/modes) | Route modules, generated route types, and framework conventions through its official Vite plugin. Use SPA mode (`ssr: false`) for the local app and `clientLoader`/`clientAction` for browser API calls. |
| Live server state | [TanStack Query](https://tanstack.com/query/latest/docs/framework/react/guides/polling) + native `fetch` | Poll active runs on the dashboard and run detail page; stop polling when a run finishes. React Router handles navigation and route loading. The API also serves `GET /api/runs/{id}/events/stream` as server-sent events. The current UI does not open that stream. |
| Code review | [CodeMirror merge view](https://codemirror.net/docs/ref/#merge) | The run detail **Tests** tab shows the original and proposed test file side by side. |
| Formatting and linting | [Biome](https://biomejs.dev/guides/configure-biome/) | One tool for TypeScript/TSX formatting and linting. `bun run check` runs it. |
| Frontend checks | [Vitest](https://vitest.dev/guide/) | `bun run test` runs Vitest. There is no Playwright suite in this repository. |

Charts use [recharts](https://recharts.org/) through the shadcn chart component on the dashboard, runs, run detail, and evaluation pages.

## Repository shape

```text
regress/
├── src/regress/       # Python engine, CLI, and local HTTP API
├── web/               # React 19 + TypeScript + Vite 8 app
│   ├── package.json
│   ├── bun.lock
│   ├── vite.config.ts
│   ├── react-router.config.ts
│   └── app/
│       ├── ...
├── examples/
├── docs/              # Fumadocs site, deployed separately
├── deploy/            # Nginx config and container entrypoint
├── pyproject.toml
└── uv.lock
```

The web app talks to the Python API over localhost. Python starts and tracks runs, invokes the existing mutation tools, reads result files, and holds `OPENAI_API_KEY`. The key must stay out of browser code and client-exposed Vite environment variables.

## What the UI does

1. The dashboard at `/` shows the served project, eligible source files, recent results, and measured mutation scores.
2. **New run** starts work in the API process. The run page polls elapsed time, activity, and errors, and can cancel the run.
3. A finished run shows the score, mutants, test snapshots, and a diff of the kept tests.

`POST /api/runs` returns `202` with the run ID immediately. The pipeline runs in its own process, so a long mutation stays visible and cancellable.

