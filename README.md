# regress

Improve AI-generated tests using mutation testing as an objective feedback loop.

A model writes tests, [StrykerJS](https://stryker-mutator.io) breaks the code in small ways (mutants), and every mutant the tests fail to notice goes back to the model as a concrete regression to catch. Regress then compares the mutation scores before and after.

```text
source ─▶ AI generates tests ─▶ validate (Vitest) ─▶ mutation test (Stryker)
                                                            │
                         compare before / after ◀── AI improves tests from surviving mutants
```

## Requirements

- Python 3.13+ and [uv](https://docs.astral.sh/uv/)
- [Bun](https://bun.sh) (runs Vitest and Stryker; falls back to `npx`) and Node.js
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

This output is from a run with a scripted model. See [Evaluation](#evaluation) for real-model numbers. While Regress waits on the model, Vitest or Stryker, a spinner shows the elapsed time. For the model it also shows what it is doing: `thinking` (with a headline when the model provides a reasoning summary) or `writing ~3,100 tokens`. If OpenAI goes quiet, the spinner says for how long (`quiet for 45s`). In logs or piped output, Regress prints a progress line every 30 seconds. A healthy response sends data every few seconds, so after 2 minutes of silence Regress reports the stall and retries, up to 3 attempts.

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

Before each run, Regress lists the newest models your API key can use and asks which one should write the tests. Press Enter to keep the default, `gpt-6-luna` unless you change it. The list comes from the OpenAI API, is cached for a day, and falls back to the list bundled with the `openai` SDK when the API is unreachable. It only shows text models (no audio, realtime, image or embedding models), but any model your key has can be typed by name.

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
regress models --all --refresh  # every model incl. dated snapshots, fetched fresh
```

Regress doesn't ask when `--model`, `--yes`, or `REGRESS_MODEL` is given, or when it isn't running in an interactive terminal (CI, pipes).

## How it works

1. **Validate the project:** `package.json`, installed Vitest/Stryker versions, the target file and its test file.
2. **Generate tests:** the model gets the source (with line numbers), the modules it imports locally, and the existing tests, and returns a complete test file as structured output.
3. **Validate the tests.** A candidate is accepted only if all of these hold:
   - it imports the real module under test and doesn't mock it
   - it has no `.only`/`.skip`/`.todo`
   - it passes against the original implementation in Vitest
   - every existing test survives with the same name
   - it adds at least one test
   - the source file is unchanged afterwards

   Rejected candidates go back to the model with the exact errors (up to `max_repairs` times). The implementation is treated as the source of truth, because these are regression tests.
4. **Mutation testing:** Stryker mutates only the target file and runs only the target test file (`testFiles`), so the score reflects what that test file alone can detect.
5. **Improve:** undetected mutants (survived or uncovered) are sent back as before/after lines:

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
```

## Evaluation

`examples/` is a small TypeScript project with seven modules (cart, password policy, intervals, token-bucket rate limiter, slugify/truncate, retry backoff, pagination), each with deliberately weak tests. It also includes **32 hidden bugs** in `examples/hidden-bugs/`. These are realistic regressions (wrong rounding, off-by-one offsets, ignored options, forgotten normalization) written to differ from Stryker's mutation operators. Mutation feedback drives the improvement, so the hidden bugs are a separate test of whether better scores mean tests that catch real bugs.

```bash
cd examples && bun install && cd ..
regress eval examples            # existing tests vs one-shot AI vs Regress (needs OPENAI_API_KEY)
regress eval examples --oracle   # validate the hidden bugs with hand-written reference suites
```

A bug counts as caught when any test fails with it applied. Results are saved to `examples/.regress/eval/<timestamp>/eval.md`.

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

```bash
uv sync
uv run pytest                      # all tests (integration tests need `bun install` in examples/)
uv run pytest -m "not integration" # fast unit tests only
uv run ruff check . && uv run ruff format .
```

The integration tests drive the full pipeline with a scripted model against real Vitest and Stryker. They cover improvement, repair of failing candidates, rejection with rollback, test file creation and hidden-bug checks.

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

## Scope and roadmap

The MVP covers TypeScript/JavaScript with Vitest and StrykerJS, one source file and one test file at a time, in local repositories, from the CLI. Next steps: an HTTP API over the Python core, then a web UI.
