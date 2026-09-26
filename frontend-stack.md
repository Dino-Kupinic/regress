# Frontend stack for Regress

**Direction:** a local browser UI on `localhost`, written in TypeScript. Keep the Python engine and add a `web/` app in this repository. The first screen is a dashboard.

## Recommended stack

| Part | Choice | Why it fits Regress |
| --- | --- | --- |
| UI | [React 19](https://react.dev/) + TypeScript | Components for the dashboard, run status, reports, and reviewing generated tests. Use React and React DOM 19.2.7 or newer for React Router 8. |
| Build and development server | [Vite 8](https://v8.vite.dev/blog/announcing-vite8) | Build and serve the local browser app with the official React Router Framework Mode integration. |
| JavaScript tooling | [Bun](https://bun.sh/docs/pm/cli/install) | Install dependencies and run scripts in `web/`. Commit `web/bun.lock`; keep `uv` for Python. The examples already use Bun. |
| Styling and components | [Tailwind CSS](https://ui.shadcn.com/docs/installation/react-router) + [shadcn/ui](https://ui.shadcn.com/docs/installation/react-router) | A consistent set of cards, tables, dialogs, badges, and progress indicators that lives in the app's source. Use the React Router setup guide for shadcn/ui. |
| Routes and route data | [React Router 8 Framework Mode](https://reactrouter.com/start/modes) | Route modules, generated route types, and framework conventions through its official Vite plugin. Use SPA mode (`ssr: false`) for the local app and `clientLoader`/`clientAction` for browser API calls. |
| Live server state | [TanStack Query](https://tanstack.com/query/latest/docs/framework/react/guides/polling) + native `fetch` | Poll active runs on the dashboard and run detail page; stop polling when a run finishes. React Router handles navigation and route loading. Add server-sent events later if live output needs lower latency. |
| Code review | [CodeMirror merge view](https://codemirror.net/docs/ref/#merge) | Show the original and proposed test changes side by side. Add this when the review screen is built. |
| Formatting and linting | [Biome](https://biomejs.dev/guides/configure-biome/) | One tool for TypeScript/TSX formatting and linting. |
| Frontend checks | [Vitest](https://vitest.dev/guide/) and [Playwright](https://playwright.dev/docs/intro) | Component and browser checks once the UI exists. |

Add [shadcn charts](https://ui.shadcn.com/docs/components/base/chart) only when a chart improves a report. A clear status table and a readable diff are more useful for the first version.

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
├── pyproject.toml
└── uv.lock
```

The web app talks to the Python API over localhost. Python starts and tracks runs, invokes the existing mutation tools, reads result files, and holds `OPENAI_API_KEY`. The key must stay out of browser code and client-exposed Vite environment variables.

## First usable workflow

1. Open the dashboard at `/`. Show the configured project, the active run and its current phase, a recent runs table, and the latest mutation score when available.
2. Start a run from the dashboard. Show elapsed time, latest activity, and errors while it runs, with a cancel action.
3. Open a run to inspect its completed report: score, surviving mutations, generated or improved test files, and a diff for each proposed change.

The API should return a run ID promptly when a run starts. The UI can then request its status and results by ID. The long-running work should execute outside the HTTP request so an eight-minute mutation is visible and cancellable rather than appearing frozen.

