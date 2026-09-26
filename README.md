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
regress run src/cart.ts      # generate, mutation-test, improve, compare
regress report               # show the latest run (or: --list, <run-id>, --json)
```

```text
regress

Analyzing src/cart.ts...

Baseline tests         3

Generated tests        2
Total tests            5

Mutation run #1 (generated tests, 3s)
Killed                44
Survived              18
No coverage           26
Score                50%

Improving tests from surviving mutations... (sending 40 of 44)
Added tests           22
Total tests           27

Mutation run #2 (improved tests, 4s)
Killed                85
Survived               3
Score                97%

Improvement         +47%

✓ Kept improved tests in test/cart.test.ts
```

(Output from a run with a scripted model; see [Evaluation](#evaluation) for real-model numbers.)

Useful flags for `regress run`:

| Flag | Meaning |
|---|---|
| `--test/-t PATH` | Test file to extend. By default it's found next to the source, in `__tests__/`, or under `test/`, `tests/` or `spec/`, and created if missing. |
| `--rounds N` | Improvement rounds after the first mutation run (default 1). |
| `--baseline` | Also mutation-test the existing tests first, for a before/after against them. |
| `--no-generate` | Skip the one-shot round and improve the existing tests directly. |
| `--model/-m` | OpenAI model (default `gpt-5.5`). |
| `--verbose/-v` | Show full validation errors and the model's summaries. |

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

`regress.toml` in the project root (written by `regress init`). Flags override it, and so do the `REGRESS_MODEL` / `REGRESS_REASONING_EFFORT` environment variables.

```toml
model = "gpt-5.5"
# reasoning_effort = "medium"
rounds = 1
max_repairs = 2
max_mutants = 40
runner = "auto"   # "bun", "npx" or "auto"
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
| `ui.py` | Terminal output |
| `store.py` | Run storage |
| `evaluation.py` | Hidden-bug evaluation |

## Scope and roadmap

The MVP covers TypeScript/JavaScript with Vitest and StrykerJS, one source file and one test file at a time, in local repositories, from the CLI. Next steps: an HTTP API over the Python core, then a web UI.
