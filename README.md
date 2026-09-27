# regress

Improve AI-generated tests using mutation testing as an objective feedback loop.

A model writes tests, [StrykerJS](https://stryker-mutator.io) breaks the code in small ways (mutants), and every mutant the tests fail to notice goes back to the model as a concrete regression to catch. Regress then compares the mutation scores before and after.

```text
source ─▶ AI generates tests ─▶ validate (Vitest) ─▶ mutation test (Stryker)
                                                            │
                         compare before / after ◀── AI improves tests from surviving mutants
```

## Documentation

The full documentation is a [Fumadocs](https://fumadocs.dev) site in [`docs/`](docs/content/docs): installation, development, using the CLI and web app, configuration, reports, the HTTP API, troubleshooting, and deployment. Run it locally with:

```bash
cd docs
bun install --frozen-lockfile
bun run dev   # http://127.0.0.1:3000
```

[Deploy the documentation](docs/content/docs/deployment/documentation.mdx) explains how to host it.

## Requirements

- Python 3.13+ and [uv](https://docs.astral.sh/uv/)
- Node.js 22 and [Bun](https://bun.sh) 1.3.13 (CI and the production image pin these). Bun runs Vitest and Stryker; `runner = "auto"` falls back to `npx` when Bun is not on `PATH`
- Linux or macOS for `regress serve`. Project locking uses `fcntl`
- A TypeScript/JavaScript project with **Vitest 2–4**, `@stryker-mutator/core` and `@stryker-mutator/vitest-runner` (`regress init` installs them)
- An OpenAI API key in `OPENAI_API_KEY` (a `.env` file works too)

> **Vitest 5 is not supported yet.** Stryker's Vitest runner (10.x) never activates mutants under Vitest 5, so every mutant silently "survives". Regress detects this and refuses to run. `regress init` offers to install `vitest@^4`.

## Install

```bash
uv tool install .
```

Or run it from a checkout with `uv run regress ...`.

## Usage

```bash
regress init                 # check the project, install Vitest + Stryker, write regress.toml
regress run src/cart.ts      # pick a model, generate, mutation-test, improve, compare
regress report               # show the latest run (or: --list, <run-id>, --json)
regress models               # list the latest models, set your default
regress serve                # HTTP API for the web app on http://127.0.0.1:8765/api
```

```text
regress

Analyzing src/cart.ts...

Baseline tests         3

✗ Attempt 1/3 rejected; asking the model to fix it
  · Test "Cart applies a percent coupon" fails against the current implementation: AssertionError: expected 8 to be 10 // Object.is equality
Generated tests        2
Total tests            5
Written by scripted in 0s · 2 attempts

Mutation run #1 (generated tests, 3s)
Killed                44
Survived              18
No coverage           26
Score                50%

Improving tests from surviving mutations... (sending 40 of 44)
Added tests           22
Total tests           27
Written by scripted in 0s

Mutation run #2 (improved tests, 4s)
Killed                85
Survived               3
Score                97%

Improvement         +47%

✓ Kept improved tests in test/cart.test.ts
```

This output is from a run with a scripted model. See [Evaluation](#evaluation) for real-model numbers. While Regress waits on the model, Vitest or Stryker, a spinner shows the elapsed time. For the model it also shows what it is doing: `thinking` (with a headline when the model provides a reasoning summary) or `writing ~3,100 tokens`. After 20 seconds without new data the spinner adds `quiet for …`. In logs or piped output, Regress prints a progress line every 30 seconds. Silence longer than `llm_timeout` (120 seconds by default) is retried, up to 3 attempts. In the terminal summary, **Killed** includes mutants that timed out; `report.json` counts those separately.

Useful flags for `regress run`:

| Flag | Meaning |
|---|---|
| `--test/-t PATH` | Test file to extend. By default it's found next to the source, in `__tests__/`, or under `test/`, `tests/` or `spec/`, and created if missing. |
| `--rounds N` | Improvement rounds after the first mutation run (default 1). |
| `--baseline` | Also mutation-test the existing tests first, for a before/after against them. |
| `--no-generate` | Skip the one-shot round and improve the existing tests directly. |
| `--model/-m` | OpenAI model for this run; skips the model question. |
| `--yes/-y` | Ask nothing; use the default model. |
| `--verbose/-v` | Show full validation errors and the model's summaries. |

### Choosing a model

Before each run, Regress shows the 10 newest text models your API key can use, plus your default if it is not already in that list, and asks which one should write the tests. Press Enter to keep the default, `gpt-6-luna` unless you change it. Dated snapshots stay hidden until `regress models --all`. The list comes from the OpenAI API and is cached for a day. If a later refresh fails and a cache already exists, Regress keeps that cache and says the API could not be reached. With no cache, it falls back to the text models bundled with the installed `openai` SDK. The short list leaves out non-text models (audio, realtime, image, embedding, and similar), shut-down models, and legacy `gpt-3.5` / `gpt-4` IDs. Any model ID your key can use can still be typed by name.

```text
Which model should write the tests? (fetched from the OpenAI API just now)
   1  gpt-6-luna              2026-09-14  newest, default
   2  gpt-6-sol               2026-09-14
   ...
Model (number or name) (gpt-6-luna): 2
Remember gpt-6-sol and stop asking? [y/n] (n): y
```

If you answer yes, Regress saves the model as your default in `~/.config/regress/config.toml` and stops asking. Manage this later with:

```bash
regress models                  # latest models, your default, and whether Regress asks
regress models --set gpt-6-sol  # change the default
regress models --ask            # ask before each run again (--no-ask to stop)
regress models --all --refresh  # older text models and dated snapshots, fetched fresh
```

Regress doesn't ask when `--model`, `--yes`, or `REGRESS_MODEL` is given, or when it isn't running in an interactive terminal (CI, pipes).

### Coding agents

[`skills/regress/`](skills/regress/SKILL.md) is an agent skill. It teaches coding agents such as Claude Code, Codex or Cursor how to set up a project, run Regress without a terminal, read `report.json` and handle failures. Install it with:

```bash
npx skills add Dino-Kupinic/regress
```

Or copy the directory into your agent's skills folder, such as `~/.claude/skills/`. See [Coding agents](docs/content/docs/using/agents.mdx).

## How it works

1. **Validate the project:** `package.json`, installed Vitest/Stryker versions, the target file and its test file.
2. **Generate tests:** the model gets the source (with line numbers), the modules it imports locally, and the existing tests, and returns a complete test file as structured output.
3. **Validate the tests.** A candidate is accepted only if all of these hold:
   - it imports the real module under test and doesn't mock it
   - it has no `.only`, and it does not add `.skip`, `.todo`, `.fails`, `.skipIf`, or `.runIf` beyond what the previous file already had
   - it passes against the original implementation in Vitest, with no errors outside the tests (a failing hook, an unhandled rejection)
   - Vitest finishes within `vitest_timeout` seconds. A test that never ends is stopped and sent back like any other problem
   - every existing test survives with the same name
   - it adds at least one test
   - the source file is unchanged afterwards

   Rejected candidates go back to the model with the exact errors (up to `max_repairs` times). The implementation is treated as the source of truth, because these are regression tests.
4. **Mutation testing:** Stryker mutates only the target file and runs only the target test file (`testFiles`), so the score reflects what that test file alone can detect.
5. **Improve:** undetected mutants (survived or uncovered) are sent back as before/after lines. At most `max_mutants` (default 40) go in one round. When there are more, Regress spreads them across the file, taking one mutant per line and operator before any duplicates:

   ```text
   Mutant 3 · EqualityOperator · src/cart.ts:31 (survived: tests ran this code but no assertion failed)
     original: if (newQuantity > MAX_QUANTITY) {
     mutated:  if (newQuantity >= MAX_QUANTITY) {
   ```

   The model may flag mutants it believes are equivalent. These are excluded from later rounds but still count in the score.
6. **Evaluate again,** keep the best-scoring valid test file, and save a report.

If a run fails or is interrupted, the original test file is restored. Every version is still saved under `.regress/runs/<id>/tests/`.

Regress runs model-written test code on your machine, just as you would when running tests an assistant wrote. Review the tests before committing them.

### Run artifacts

Each run writes `.regress/runs/<timestamp>-<name>/`. The directory ignores itself in git.

| Path | Contents |
|---|---|
| `report.json` | Stages, test counts, every mutant with its status, token usage |
| `events.jsonl` | Progress log, for runs started over the [HTTP API](#http-api) |
| `tests/` | Each accepted version of the test file |
| `llm/` | Every prompt and structured response |
| `stryker/`, `vitest/` | Tool configs, JSON reports and logs |

## Configuration

Settings are read in this order, with later sources winning:

1. built-in defaults
2. your user config (`~/.config/regress/config.toml`, written by `regress models`)
3. the project's `regress.toml` (written by `regress init`)
4. `REGRESS_MODEL` / `REGRESS_REASONING_EFFORT`
5. command-line flags

Both files accept the same keys:

```toml
# model = "gpt-6-luna"   # in regress.toml this pins the model for everyone on the project
ask_model = true      # ask which model to use before each run
# reasoning_effort = "medium"
rounds = 1
max_repairs = 2
max_mutants = 40
runner = "auto"       # "bun", "npx" or "auto"
llm_timeout = 120     # seconds without any data from the model before retrying
llm_max_duration = 1800  # total budget for one proposal, including retries
llm_max_output_tokens = 32768  # output budget per request, including reasoning
vitest_timeout = 300  # seconds a Vitest run may take before a candidate counts as hanging
stryker_timeout = 1800  # seconds for one mutation run
```

## HTTP API

`regress serve` runs a local HTTP API over the same engine, for the web app (see [frontend-stack.md](frontend-stack.md)). It serves the project that contains the given directory:

```bash
regress serve examples       # http://127.0.0.1:8765/api, interactive docs at /api/docs
```

The OpenAPI schema at `/api/openapi.json` describes every request and response, so the web app can generate its types from it (for example with `openapi-typescript`). Operation IDs are the route names: `start_run`, `get_run`, and so on. Evaluations (`/api/evaluations`) are documented in the [HTTP API reference](docs/content/docs/reference/http-api.mdx); the table below is the run and project surface.

| Method | Path | What it does |
|---|---|---|
| `GET` | `/api/health` | Liveness and API version |
| `GET` | `/api/ready` | Readiness: configured project, installed tools, API key, writable artifact storage (503 if unavailable) |
| `GET` | `/api/project` | The project and whether a run can start: packages, toolchain, API key, active run |
| `POST` | `/api/project/init` | Install what's missing and write `regress.toml`, like `regress init --yes` |
| `GET` | `/api/project/sources` | Source files a run can target |
| `GET` | `/api/project/sources/{path}` | A source file and the test file a run would extend or create |
| `GET` | `/api/project/files/{path}` | A JavaScript or TypeScript file's contents |
| `GET`, `PATCH` | `/api/settings` | Settings and where each came from; `PATCH` changes your user config |
| `GET` | `/api/models` | Models to choose from (`?refresh=true`, `?all=true`) |
| `GET` | `/api/runs` | All runs, newest first (`?source=src/cart.ts`, `?limit=`) |
| `POST` | `/api/runs` | Start a run. Returns `202` with the run's ID at once |
| `GET` | `/api/runs/{id}` | Summary and report, and while it runs, what it is doing (`live`) |
| `POST` | `/api/runs/{id}/cancel` | Stop the run and restore the test file |
| `DELETE` | `/api/runs/{id}` | Delete a finished run's artifacts |
| `GET` | `/api/runs/{id}/events` | The run's log. Poll with `?after=<last seq>` |
| `GET` | `/api/runs/{id}/events/stream` | The log and live activity as server-sent events |
| `GET` | `/api/runs/{id}/diff` | The original test file against the one the run kept |
| `GET` | `/api/runs/{id}/stages/{n}/tests`, `…/diff`, `…/mutants` | A stage's test file, what it changed, and its mutants with the line before and after |
| `GET` | `/api/runs/{id}/llm`, `/api/runs/{id}/llm/{name}` | Model calls with token usage, and each full prompt and response |
| `GET` | `/api/runs/{id}/artifacts`, `…/artifacts/{path}` | Any file in the run directory |

The body of `POST /api/runs` takes the options of `regress run`: `source`, `test`, `model`, `rounds`, `baseline`, `generate` and `runner`.

```bash
curl -X POST localhost:8765/api/runs -H 'content-type: application/json' \
  -d '{"source": "src/cart.ts", "baseline": true}'
```

The run goes on in the background, one at a time: starting another returns `409` until it is over. Follow it from the browser:

```ts
const stream = new EventSource(`/api/runs/${id}/events/stream`);
stream.addEventListener("run-event", (e) => addToLog(JSON.parse(e.data))); // RunEvent: seq, type, message, data
stream.addEventListener("live", (e) => showActivity(JSON.parse(e.data))); // LiveState: activity, elapsed_seconds
stream.addEventListener("end", (e) => {
  showResult(JSON.parse(e.data)); // RunSummary
  stream.close(); // otherwise EventSource reconnects
});
```

Each run executes in a process of its own. Cancelling sends it SIGINT, like Ctrl-C in `regress run`: the model call, Vitest or Stryker stops at once, the test file is restored, and the run ends as `cancelled`. Stopping the server cancels a run in progress the same way.

The API defaults to local access. For hosting, the bundled [Nginx deployment](docs/content/docs/deployment/application.mdx) provides authentication:

- It listens on 127.0.0.1 and answers only requests addressed to a local host name, which stops DNS rebinding.
- Browsers can call it only from allowed origins: `http://localhost:5173` and `http://127.0.0.1:5173` by default. `--origin` is repeatable and replaces that pair. Requests that change something and come from another origin get `403`, including simple requests that skip the CORS preflight. `GET`, `HEAD`, and `OPTIONS` pass that check, as do clients that send no `Origin` header, such as `curl`.
- `OPENAI_API_KEY` stays on the server. Only JavaScript and TypeScript files outside hidden and dependency folders can be read, so `.env` is never served.
- Binding beyond loopback requires `--allow-remote`. Keep the API port private and expose only the authenticated proxy.

API runs, evaluations and dependency installs share one exclusive project reservation. A second API server does not take the project while the first still holds it. It answers readiness checks during that wait, then exits if the lock is still held after 30 seconds. Do not run CLI mutation jobs against a project currently served by the API. The backend's process supervision and project locking require Linux or macOS.

Requests have a 1 MiB body limit. Unexpected errors return a generic response with an `X-Request-ID` that also appears in server logs. Tool output retained in each log is capped at the most recent 1 MiB. Reports and configuration use atomic file replacement, and cancellation restores the original source and test bytes.

New API runs also save recovery records before starting. On restart after a hard stop, unfinished API runs restore their original files and become failed runs; completed results remain intact.

In development, let Vite proxy `/api` so the browser sees a single origin:

```ts
// vite.config.ts
export default defineConfig({ server: { proxy: { "/api": "http://127.0.0.1:8765" } } });
```

## Evaluation

`examples/` is a small TypeScript project with seven modules (cart, password policy, intervals, token-bucket rate limiter, slugify/truncate, retry backoff, pagination), each with deliberately weak tests. It also includes **32 hidden bugs** in `examples/hidden-bugs/`. These are realistic regressions (wrong rounding, off-by-one offsets, ignored options, forgotten normalization) written to differ from Stryker's mutation operators. Mutation feedback drives the improvement, so the hidden bugs are a separate test of whether better scores mean tests that catch real bugs.

```bash
cd examples && bun install && cd ..
regress eval examples            # existing tests vs one-shot AI vs Regress (needs OPENAI_API_KEY)
regress eval examples --oracle   # validate the hidden bugs with hand-written reference suites
```

A bug counts as caught when any test fails with it applied. Results are saved to `examples/.regress/eval/<timestamp>/eval.md`.

If the model never writes a valid test file for a module, the run keeps the existing tests. The module's One-shot AI and Regress columns then count as those existing tests, with a note, so a module the model fails on still counts against it. Totals only count modules with a result in every column, so each column adds up the same modules. A module that fails for another reason, such as an API outage, is shown with its error and left out, and the totals row says how many modules it covers.

The oracle check needs no model. It confirms every hidden bug is detectable, and that the existing tests catch none of them:

| Module | Existing tests score | Existing tests bugs | Oracle score | Oracle bugs |
|---|---:|---:|---:|---:|
| backoff | 31% | 0/5 | 93% | 5/5 |
| cart | 36% | 0/5 | 97% | 5/5 |
| intervals | 38% | 0/4 | 92% | 4/4 |
| pagination | 0% | 0/4 | 100% | 4/4 |
| password | 28% | 0/5 | 85% | 5/5 |
| rate-limiter | 20% | 0/4 | 92% | 4/4 |
| slugify | 30% | 0/5 | 96% | 5/5 |
| **All modules** | **26%** | **0/32** | **94%** | **32/32** |

## Development

For a hosted demo, see [Deploy the application](docs/content/docs/deployment/application.mdx). [Develop Regress](docs/content/docs/getting-started/development.mdx) covers the repository layout and every check.

```bash
uv sync
uv run pytest                      # all tests (integration tests need `bun install` in examples/)
uv run pytest -m "not integration" # fast unit tests only
uv run ruff check src tests
uv run ruff format --check src tests
```

The integration tests drive the full pipeline with a scripted model against real Vitest and Stryker. They cover improvement, repair of failing candidates, rejection with rollback, test file creation, hidden-bug checks, and runs started, streamed and cancelled over HTTP.

The Backend GitHub Actions workflow runs lint, formatting, unit and integration tests, then builds the production container and smoke-tests readiness and proxy authentication. Tests use scripted models and do not call OpenAI.

Code map (`src/regress/`):

| Module | Role |
|---|---|
| `cli.py` | Typer commands |
| `pipeline.py` | The generate → validate → mutate → improve loop |
| `project.py` | Project, toolchain and test-file discovery |
| `vitest.py`, `stryker.py` | Tool adapters |
| `validate.py` | Candidate checks |
| `mutants.py`, `prompts.py`, `llm.py` | Mutant selection, prompts, OpenAI |
| `catalog.py` | Available models: fetch, cache, filter |
| `config.py` | Layered settings and the user config |
| `ui.py` | Terminal output |
| `store.py` | Run storage |
| `evaluation.py` | Hidden-bug evaluation |
| `api/` | HTTP API: app and security (`app.py`), routes, run processes (`jobs.py`), saved runs (`results.py`), evaluations (`evaluations.py`) |

## Scope and roadmap

Regress covers TypeScript/JavaScript with Vitest and StrykerJS, one source file and one test file at a time, through the CLI or web app. Hosted deployments serve one trusted project and one operator group per instance. Independent users or untrusted repositories require separate isolated workers, credentials and storage.
