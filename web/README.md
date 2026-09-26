# Regress web app

The React Router SPA uses the Python API on `127.0.0.1:8765`. Vite proxies `/api`, so browser requests stay on the local app origin. The OpenAI API key is read only by Python.

```bash
# Terminal 1, from the repository root
uv run regress serve examples

# Terminal 2
cd web
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. To check the frontend, run `npm run typecheck`, `npm run check`, and `npm run build` in `web/`.

Routes: dashboard `/`, runs `/runs`, run detail `/runs/:id`, evaluation `/evaluation`, settings `/settings`, and project setup `/setup`.

The dashboard and report screens show only data returned by the API. A new run starts in the background and its detail page polls status and events. Evaluation results are read from `.regress/eval/`; the Evaluation page can start an oracle or full benchmark through the local API. Settings save personal defaults through `PATCH /api/settings`. Project setup calls `POST /api/project/init` and directs the user to set the API key in the Python environment.
