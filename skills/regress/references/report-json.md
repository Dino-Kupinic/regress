# report.json

Each run writes `.regress/runs/<run-id>/report.json` in the project. `regress report RUN_ID --json` prints the same file. The run ID is a timestamp followed by the source file's lowercased name, such as `20260926-230928-defu`.

## Run directory

| Path | Contents |
| --- | --- |
| `report.json` | The report described below. |
| `tests/` | The test file at each stage, numbered in order: `0-baseline.test.ts`, `1-generated.test.ts`, `2-improved.test.ts`, and so on. The files keep the test file's own suffix, such as `.spec.ts`. |
| `llm/` | Every prompt (`NN-<stage>-attemptN.prompt.md`) and structured response (`….response.json`). |
| `vitest/` | The JSON report and log of each validation run. The existing-test run is `baseline`. |
| `stryker/` | `mutation-N.json`, `stryker-N.config.json` and `stryker-N.log` for the Nth mutation run. N counts mutation runs in order; it is not the stage index. |
| `events.jsonl` | Progress log. Only runs started over the HTTP API have one. |

## Top level

| Field | Meaning |
| --- | --- |
| `id`, `created_at` | Run ID and start time, with time zone. |
| `project_root` | Absolute path of the project. |
| `source_file`, `test_file` | Paths relative to `project_root`. |
| `test_file_existed` | Whether the test file existed before the run. If `false`, Regress created it. |
| `model` | The model that wrote the tests. |
| `status` | `running`, `completed`, `failed` or `cancelled`. |
| `error` | Why the run failed or was cancelled, otherwise `null`. |
| `stages[]` | One entry per version of the test file, in order. The first is always the existing tests, even when there were none. |
| `kept_stage` | The `label` of the stage whose test file is in the project now. `null` means the original file was restored. |
| `usage` | `calls`, `input_tokens` and `output_tokens` for the whole run. |
| `duration_seconds` | Wall-clock time. |

## `stages[]`

| Field | Meaning |
| --- | --- |
| `kind` | `baseline` (the existing tests), `generated` (one-shot proposal) or `improved` (a round driven by mutants). |
| `label` | `Existing tests`, `Generated tests`, `Improved tests`, or `Improved tests (round N)` when there are several rounds. |
| `test_file_snapshot` | Path of this version inside the run directory, such as `tests/1-generated.test.ts`. |
| `test_count`, `test_names` | The tests in this version. |
| `mutation` | The Stryker result (see below). `null` when this version was not mutation-tested. The baseline is measured only with `--baseline` or `--no-generate`. |
| `summary` | The model's description of what it added. |
| `attempts`, `llm_seconds` | How many proposals the stage took, and the time spent waiting on the model. |
| `targeted_mutants` | IDs of the mutants this improvement was asked to catch. |
| `equivalent_mutants` | IDs the model claims cannot change behaviour. They still count in the score. |
| `rejected`, `problems` | `rejected` is `true` when every attempt of an improvement round failed validation. `problems` lists why: a failing test, a renamed or removed existing test, a mocked target module, an added `.only` or `.skip`, an edited source file, and so on. A generation that fails every attempt records no stage. The run fails instead, with the reason in `error`. |

## `stages[].mutation`

| Field | Meaning |
| --- | --- |
| `score` | (killed + timeout) / (killed + timeout + survived + no_coverage) × 100. `0.0` when no mutant was valid. |
| `killed`, `timeout` | Detected mutants. A timeout usually means the mutant caused an infinite loop. |
| `survived` | Tests ran the mutated code, but none failed. |
| `no_coverage` | No test ran the mutated code. |
| `errors` | Compile or runtime errors. These are excluded from the score. |
| `duration_seconds` | Time the Stryker run took. |
| `mutants[]` | Every mutant: `id`, `mutator` (such as `EqualityOperator`), `status` (`Killed`, `Survived`, `NoCoverage`, `Timeout`, `CompileError`, `RuntimeError`, `Ignored`, `Pending`), `original` and `replacement` code, `start_line`/`start_column`/`end_line`/`end_column` (1-based), `killed_by`, `covered_by`, and `status_reason`. |

## Recipes

```bash
R=.regress/runs/RUN_ID/report.json

# Did it work, and what is in the project now?
jq '{status, error, kept_stage, test_file, test_file_existed}' "$R"

# Score and test count per stage
jq -r '.stages[] | [.label, (if .rejected then "rejected" elif .mutation then (.mutation.score | round | tostring) + "%" else "not measured" end), .test_count] | @tsv' "$R"

# Mutants the kept tests still miss
jq -r '.kept_stage as $k | .stages[] | select(.label == $k) | .mutation.mutants[]
  | select(.status == "Survived" or .status == "NoCoverage")
  | "line \(.start_line) \(.mutator) \(.status): \(.original) -> \(.replacement)"' "$R"

# Why an improvement round was rejected
jq -r '.stages[] | select(.rejected) | .label, (.problems[] | "  - " + .)' "$R"

# Tests the run added to the file
jq -r '(.stages[0].test_names) as $before | .kept_stage as $k
  | .stages[] | select(.label == $k) | .test_names - $before | .[]' "$R"
```

The first stage is always `Existing tests`. When the test file did not exist, it has no tests and no snapshot, so the last recipe lists every test in the kept file.
