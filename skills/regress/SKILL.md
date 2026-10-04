---
name: regress
description: Strengthen the Vitest tests for one TypeScript or JavaScript source file with Regress, a CLI that mutation-tests the file with StrykerJS and has an OpenAI model write tests that catch the mutants that survived. Use when asked to run regress, to generate or improve tests for a TS/JS file with mutation testing, to raise a mutation score, or to read a Regress run report (`.regress/runs/`, `report.json`).
---

# Regress

Regress improves the test file for **one source file at a time**. It can run several files in a row, but each file gets its own run:

1. An OpenAI model writes tests for the source file.
2. StrykerJS makes small deliberate bugs in that file (mutants).
3. Every mutant the tests fail to catch goes back to the model as a regression to cover.

Regress keeps the best valid test file and reports the mutation score before and after.

It supports TypeScript and JavaScript projects with **Vitest 2–4**, run through Bun or npx. It does not support Jest, Mocha, Vitest 5, other languages, or mutating several files in one Stryker run.

## Ground rules

- **Runs cost money.** `regress run` and `regress eval` (without `--oracle`) call the OpenAI API on the user's key. Start a run only when the user asked for one. Ask before running Regress over several files.
- **Runs execute model-written test code** on this machine, as any test an assistant writes would.
- **Regress rewrites the test file.** A run that fails or is interrupted restores the original file. A run that completes leaves the kept version in place, uncommitted. Check `git status` first, so the diff you show afterwards is only Regress's.
- **Run one job per project at a time.** Don't run two runs at once. Don't start a CLI run while `regress serve` is serving the same project. Don't edit, format or watch-rebuild the source file during a run: Regress aborts if it changes.
- Never print or log `OPENAI_API_KEY`.

## 1. Check the setup

```bash
regress --help
```

If `regress` isn't installed, install it with `uv tool install git+https://github.com/Dino-Kupinic/regress`. Or run `uv tool install .` from a checkout, or prefix commands with `uv run` inside one. Regress needs Python 3.13+, Node.js, and Bun or npx on `PATH`.

The project needs `vitest` (2–4), `@stryker-mutator/core` and `@stryker-mutator/vitest-runner` installed in `node_modules`. Listing them in `package.json` is not enough. To see what's missing without installing anything, run `init` without `--yes` and with no input:

```bash
regress init path/to/project < /dev/null
```

This prints each package with its version or `missing`. If anything is missing, it prints the install command it would run, then stops at the confirmation with exit code 1. If nothing is missing, it writes `regress.toml` (if absent) and `.regress/`, and succeeds.

To install what's missing:

```bash
regress init path/to/project --yes
```

This uses the project's package manager (from its lockfile), so it changes `package.json` and the lockfile. It installs `vitest@^4` in place of a Vitest 5. If the user didn't ask you to set Regress up, tell them what it will install first.

`OPENAI_API_KEY` must be exported, or set in a `.env` file in the directory you run `regress` from or a parent directory. `regress init` warns when it is missing. If the key is missing, ask the user to set it; never ask them to paste it into the chat.

## 2. Choose the file

- Pass the **implementation**, such as `src/cart.ts`, not `test/cart.test.ts`. Extensions: `.ts .tsx .mts .cts .js .jsx .mjs .cjs`.
- Paths are relative to your shell's working directory. The project is the nearest directory above the source that has a `package.json`. In a monorepo, that is the package.
- **Test file discovery.** Regress looks for `<name>.test.*` or `<name>.spec.*` next to the source, in `__tests__/`, and in `test/`, `tests/` or `spec/` (mirroring `src/`). Failing that, it takes any file with that name that imports the source. If there is none, it creates `<name>.test.<ext>` under `test/` or `tests/` (mirroring `src/`) when one of them exists, or else next to the source. Pass `--test PATH` to choose. You must pass it when Regress reports several candidates.
- **Existing tests must pass** against the current code before a run can start.
- **Good targets** are modules with real logic: branches, arithmetic, parsing, validation. Large files produce many mutants, and Stryker runs the tests once per mutant, so large files are slow. Type-only files and barrel re-exports gain little.

## 3. Run

```bash
regress run src/cart.ts --yes
```

Always pass `--yes` (use the configured default model) or `--model MODEL_ID`, so the run never waits on the model question.

| Flag | Use it when |
| --- | --- |
| `--baseline` | The user wants a before/after against their current tests. It mutation-tests the existing tests first. |
| `--no-generate` | You want to keep the existing test file and only add to it. It skips the one-shot generation and improves the existing tests directly. It needs a passing test file with at least one test. |
| `--rounds N` | You want more (or no) improvement rounds after the first mutation run. The range is 0–5 and the default is 1. `0` generates and measures only. |
| `--test PATH` | Discovery picks the wrong file, or several candidates exist. |
| `--model ID` | The user named a model. `regress models` lists the models their key can use. |
| `--verbose` | You need the full validation errors and the model's summaries. |
| `--changed REF` | The user wants to test what a branch changed. It picks the source files changed since the merge base with `REF` (committed, uncommitted and untracked) and mutates only their changed lines. Each file costs model calls, so confirm the list with the user first. |

Several sources or a directory (`regress run src/ --yes`) run one file after another. A failing file is recorded and the rest still run; the command then exits with code 1. The combined summary is in `.regress/batches/<id>.json`.
| `--runner bun\|npx` | You need to force the runner. The default, `auto`, prefers Bun. |

**It takes minutes.** Small modules typically take 1–5 minutes, and big files take longer. A proposal may take `llm_max_duration` (30 minutes) and a Stryker run `stryker_timeout` (30 minutes) before Regress gives up. So give the command a long timeout, or run it in the background with its output sent to a file. Without a terminal, Regress prints a progress line every 30 seconds. Don't kill a run because it looks quiet. If you must stop it, send SIGINT (Ctrl-C). Regress then restores the test file and exits with 130.

Exit codes: `0` means the run completed. `1` means an error (message on stderr). `130` means it was interrupted.

A completed run ends like this:

```text
Improvement         +47%

✓ Kept improved tests in test/cart.test.ts
Report saved to .regress/runs/20260927-120000-cart · view it with `regress report`
```

The last path segment is the **run ID**. Keep it. Errors found before the run starts (missing packages, missing key, bad path) save no report.

## 4. Read the result

```bash
regress report RUN_ID                  # summary, stage table, undetected mutants
regress report RUN_ID --mutants 50     # list more undetected mutants
regress report RUN_ID --json           # raw report.json
regress report --list                  # every saved run
```

Run these from inside the project, or add `--project PATH`. Without an ID, `report` shows the latest run. A partial ID matches the latest run whose ID contains it. Prefer the full ID.

Quick answers with `jq` (field details are in [references/report-json.md](references/report-json.md)):

```bash
R=.regress/runs/RUN_ID/report.json
jq '{status, error, kept_stage, test_file}' "$R"
jq -r '.stages[] | [.label, (if .rejected then "rejected" elif .mutation then (.mutation.score | round | tostring) + "%" else "not measured" end), .test_count] | @tsv' "$R"
jq -r '.kept_stage as $k | .stages[] | select(.label == $k) | .mutation.mutants[]
  | select(.status == "Survived" or .status == "NoCoverage")
  | "line \(.start_line) \(.mutator) \(.status): \(.original) -> \(.replacement)"' "$R"
```

How to read it:

- `status` is `completed`, `failed` or `cancelled`. `error` says why a run failed.
- `kept_stage` is the stage whose test file is now in the project. If it is `null`, nothing was kept and the original file was restored.
- **Score** = (killed + timeout) / (killed + timeout + survived + no coverage). Compile and runtime errors don't count. In the terminal, **Killed** includes timeouts.
- **Improvement** is the kept score minus the generated tests' score. With `--no-generate`, it is measured against the existing tests. With `--baseline`, the terminal also shows `vs existing tests`.
- The **generated tests replace the existing file** when they pass validation, even if they score lower than a measured baseline. Every existing test must survive with the same name, but the file may be rewritten. An improvement is kept only if its score is at least the previous score.
- The model may call mutants **equivalent** (no behaviour change). They are listed in `equivalent_mutants`, but they still count against the score.
- The score describes how well **this one test file** pins down **this one source file**. It is not project coverage, and a high score doesn't prove the code is correct.

## 5. Hand it back

1. Show the change: `git diff -- TEST_FILE`, or the new file if the test file didn't exist before.
2. Review the tests. Regress treats the current implementation as the source of truth, so its tests lock in current behaviour, bugs included. If a test asserts something that looks wrong, point it out instead of silently keeping it.
3. Run the project's own test command. Regress ran only the one test file.
4. Report the score before and after, how many tests were added, and the undetected mutants worth a look. Include token usage (`.usage`) if the user cares about cost.
5. Don't commit `.regress/`; it ignores itself in Git. Don't commit the test file unless the user asks you to.

## When a run fails

Start with the error message, then look at `regress report RUN_ID` and the run directory. The run directory holds `llm/` (every prompt and response), `vitest/` and `stryker/` (tool logs) and `tests/` (every version of the test file).

| Message | What to do |
| --- | --- |
| `Missing dev dependencies: …` | Run `regress init PATH --yes` (see step 1). |
| `vitest 5.x is not supported …` or `too old for Stryker` | Install Vitest 4. `init --yes` does it. This changes the user's Vitest major version, so tell them. |
| `No package.json found …` | Pass a path inside the JS/TS project. |
| `… looks like a test file` | Pass the implementation file instead. |
| `Several candidate test files found` | Rerun with `--test PATH`. |
| The existing tests fail | Regress needs a passing baseline. Tell the user, or fix the tests if that is part of the task. |
| `--no-generate needs an existing test file …` | Drop `--no-generate`. |
| `The model did not produce valid tests in N attempts` | The terminal lists each attempt's problems (`--verbose` shows them in full). The Vitest log of each attempt is in `vitest/`, and the model's responses are in `llm/`. If this happens during generation, the run fails. If it happens in an improvement round, the run completes with the previous tests and records a rejected stage with its `problems`. Try a stronger `--model`, a higher `reasoning_effort`, or a higher `max_repairs`. |
| `The model did not finish` (length) | Raise `llm_max_output_tokens`. |
| `OPENAI_API_KEY is not set`, the key was rejected, or the quota is exhausted | The user has to fix the key or its billing. |
| `… is not available to your API key` | Pick a model from `regress models`. |
| `The source file changed …` | Stop formatters and watchers that touch the file, then rerun. |
| `Stryker failed …` or a timeout | Read `stryker/stryker-N.log`. Raise `stryker_timeout`, or test a smaller module. |
| `Bun is not installed` | Use `--runner npx`, or `runner = "auto"`. |

## Configuration

Settings come from these sources, lowest to highest priority: built-in defaults, `~/.config/regress/config.toml`, the project's `regress.toml`, `REGRESS_MODEL` / `REGRESS_REASONING_EFFORT`, and command-line flags. The main keys in `regress.toml`:

```toml
# model = "..."            # pins the model for everyone on the project
# reasoning_effort = "medium"
rounds = 1                 # 0–5
max_repairs = 2            # retries after a rejected proposal (2 means 3 attempts)
max_mutants = 40           # undetected mutants sent to the model per round
runner = "auto"            # "bun", "npx" or "auto"
# llm_timeout = 120        # seconds of model silence before a retry
# llm_max_output_tokens = 32768
# vitest_timeout = 300
# stryker_timeout = 1800
```

Values are type-checked: write `rounds = 2`, not `"2"`. `regress models` lists the models the key can use (fetching the list is free). `regress models --set ID` changes the user's personal default. Only change it if the user asks.

## Other entry points

- **Evaluation** (inside the Regress repository): `regress eval examples --oracle` checks the hidden-bug benchmark without a model, for free. `regress eval examples --yes` runs Regress on every example module and is paid. `--module NAME` limits it, and there `-m` means module, so spell out `--model`. Results go to `examples/.regress/eval/<timestamp>/`.
- **HTTP API**: `regress serve PATH` serves `http://127.0.0.1:8765/api`. The OpenAPI schema is at `/api/openapi.json` and interactive docs are at `/api/docs`. `POST /api/runs` with `{"source": "src/cart.ts"}` (relative to the project root, plus optional `test`, `model`, `rounds`, `baseline`, `generate`, `runner`) returns `202` with the run ID. Poll `GET /api/runs/{id}`, or stream `GET /api/runs/{id}/events/stream`. Starting a second job returns `409`. `POST /api/runs/{id}/cancel` stops a run and restores the test file.
