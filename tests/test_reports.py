import json
from datetime import datetime

from conftest import load_fixture

from regress.evaluation import EvalResult, ModuleResult, StageResult, to_markdown
from regress.models import MutantStatus, MutationRun, TestStatus
from regress.stryker import parse_stryker_report, source_span
from regress.vitest import parse_vitest_report


def test_parses_passing_vitest_report():
    result = parse_vitest_report(load_fixture("vitest-pass.json"))
    assert result.success
    assert result.total == 2
    assert result.names == {"cart adds", "cart totals"}
    assert all(t.status == TestStatus.PASSED for t in result.tests)


def test_compile_errors_become_suite_errors_without_ansi():
    result = parse_vitest_report(load_fixture("vitest-compile-error.json"))
    assert not result.success
    assert result.total == 0
    assert "Expected `,` or `)`" in result.suite_errors[0]
    assert "\x1b[" not in result.suite_errors[0]


def test_failed_assertion_makes_run_unsuccessful():
    report = load_fixture("vitest-pass.json")
    assertion = report["testResults"][0]["assertionResults"][0]
    assertion["status"] = "failed"
    assertion["failureMessages"] = ["AssertionError: expected 1 to be 2"]
    result = parse_vitest_report(report)
    assert not result.success
    assert [t.full_name for t in result.failed] == ["cart adds"]


def test_empty_vitest_report_is_an_error():
    result = parse_vitest_report({"success": True, "testResults": []})
    assert not result.success
    assert "no test file" in result.suite_errors[0]


UNHANDLED_OUTPUT = """\
 RUN  v4.1.11 /work/examples

····
⎯⎯⎯⎯⎯⎯ Unhandled Errors ⎯⎯⎯⎯⎯⎯

Vitest caught 1 unhandled error during the test run.

⎯⎯⎯⎯ Unhandled Rejection ⎯⎯⎯⎯⎯
AssertionError: expected 1 to be 2 // Object.is equality
 ❯ Assertion.__VITEST_RESOLVES__ ../node_modules/@vitest/expect/dist/index.js:1710:17
 ❯ test/cart.test.ts:22:55
     22|   it("extra", async () => { expect(Promise.resolve(1)).resolves.toBe(2)
       |                                                       ^
 ❯ ../node_modules/@vitest/runner/dist/chunk-artifact.js:302:11
⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯

 Test Files  1 passed (1)
      Tests  2 passed (2)
     Errors  1 error

JSON report written to /work/examples/.regress/report.json
"""


def test_unhandled_errors_fail_the_run_even_when_every_test_passes():
    # Vitest's JSON report says success, but the process exits 1 for the unhandled rejection.
    result = parse_vitest_report(load_fixture("vitest-pass.json"), UNHANDLED_OUTPUT, returncode=1)
    assert not result.success
    assert not result.failed and not result.suite_errors
    [error] = result.run_errors
    assert error.startswith("⎯⎯⎯⎯⎯⎯ Unhandled Errors")
    assert "expected 1 to be 2" in error and "test/cart.test.ts:22:55" in error
    assert "node_modules" not in error and "Test Files" not in error


def test_failing_hook_fails_the_run_without_a_failed_test():
    report = {**load_fixture("vitest-pass.json"), "success": False}
    result = parse_vitest_report(report, "no recognizable section\n", returncode=1)
    assert not result.success
    assert result.run_errors == ["Vitest exited with code 1 although no test failed.\nno recognizable section"]


def test_failed_tests_are_not_reported_again_as_run_errors():
    report = load_fixture("vitest-pass.json")
    report["testResults"][0]["assertionResults"][0]["status"] = "failed"
    assert parse_vitest_report(report, UNHANDLED_OUTPUT, returncode=1).run_errors == []


def test_parses_stryker_report_with_original_snippets():
    run = parse_stryker_report(load_fixture("stryker-report.json"), "src/cart.ts", index=1)
    assert len(run.mutants) == 15
    by_id = {m.id: m for m in run.mutants}
    assert by_id["8"].original == "qty + 1"
    assert by_id["8"].replacement == "qty - 1"
    assert by_id["8"].status == MutantStatus.KILLED
    assert by_id["3"].original == "qty >= max"
    assert by_id["3"].status == MutantStatus.SURVIVED
    # Sorted by position in the file.
    assert [m.start_line for m in run.mutants] == sorted(m.start_line for m in run.mutants)


def test_mutation_score_matches_strykers_formula():
    run = parse_stryker_report(load_fixture("stryker-report.json"), "src/cart.ts", index=1)
    assert (run.killed, run.survived, run.no_coverage) == (8, 4, 3)
    assert run.score == 100 * 8 / 15
    assert len(run.undetected) == 7


def test_score_counts_timeouts_as_detected_and_ignores_errors():
    run = MutationRun(index=1, mutants=[])
    base = {
        "mutator": "x",
        "replacement": "",
        "original": "",
        "start_line": 1,
        "start_column": 1,
        "end_line": 1,
        "end_column": 1,
    }
    statuses = ["Killed", "Timeout", "Survived", "NoCoverage", "CompileError", "Ignored"]
    run = MutationRun.model_validate(
        {"index": 1, "mutants": [{**base, "id": str(i), "status": s} for i, s in enumerate(statuses)]}
    )
    assert run.score == 50.0
    assert run.errors == 1


def test_source_span_handles_multiline_locations():
    lines = ["function f() {\n", "  return 1;\n", "}\n"]
    assert source_span(lines, 1, 14, 3, 2) == "{\n  return 1;\n}"
    assert source_span(lines, 2, 3, 2, 11) == "return 1"


def test_eval_markdown_keeps_multiline_errors_on_one_table_row():
    result = EvalResult(
        created_at=datetime.now(),
        mode="regress",
        modules=[ModuleResult(module="cart", error="The existing tests do not pass.\n  - a | b")],
    )
    row = next(line for line in to_markdown(result).splitlines() if line.startswith("| cart"))
    assert "⚠ The existing tests do not pass. - a \\| b |" in row


def _stage(label: str, score: float, caught: int, missed: int) -> StageResult:
    return StageResult(
        label=label, score=score, caught=[f"c{i}" for i in range(caught)], missed=[f"m{i}" for i in range(missed)]
    )


def test_eval_totals_add_up_the_same_modules_in_every_column():
    complete = ModuleResult(
        module="cart",
        stages=[_stage("Existing tests", 30, 0, 5), _stage("One-shot AI", 90, 5, 0), _stage("Regress", 95, 5, 0)],
    )
    # Its AI columns are missing (an API error, say): counting its existing tests alone would skew the columns.
    partial = ModuleResult(module="slugify", stages=[_stage("Existing tests", 20, 0, 4)], error="API down")
    failed = ModuleResult(module="backoff", error="Stryker failed")
    result = EvalResult(created_at=datetime.now(), mode="regress", modules=[complete, partial, failed])

    assert result.counted_modules == ["cart"]
    assert [(t.label, t.score, t.caught, t.bugs_total) for t in result.totals] == [
        ("Existing tests", 30, 0, 5),
        ("One-shot AI", 90, 5, 5),
        ("Regress", 95, 5, 5),
    ]
    assert result.totals_label == "1 of 3 modules"
    markdown = to_markdown(result)
    assert "| **1 of 3 modules** | **30%** | **0/5** | **90%** | **5/5** | **95%** | **5/5** |" in markdown
    assert "Totals count only modules with a result in every column" in markdown
    # Totals are part of the saved JSON (and the API's responses), so every client shows the same numbers.
    saved = json.loads(result.model_dump_json())
    assert saved["counted_modules"] == ["cart"] and saved["totals"][1]["caught"] == 5
    assert EvalResult.model_validate_json(result.model_dump_json()).totals == result.totals


def test_eval_markdown_explains_modules_counted_as_their_existing_tests():
    rejected = ModuleResult(
        module="rate-limiter",
        stages=[_stage(label, 20, 0, 4) for label in ("Existing tests", "One-shot AI", "Regress")],
        note="The model did not produce valid tests in 3 attempts.",
    )
    result = EvalResult(created_at=datetime.now(), mode="regress", modules=[rejected])

    assert result.totals_label == "All modules"
    markdown = to_markdown(result)
    assert "| **All modules** | **20%** | **0/4** | **20%** | **0/4** | **20%** | **0/4** |" in markdown
    assert "- **rate-limiter:** The model did not produce valid tests in 3 attempts." in markdown
    assert "Totals count only" not in markdown


def test_vitest_report_with_an_empty_suite_cannot_succeed():
    result = parse_vitest_report({"success": True, "testResults": [{"assertionResults": []}]})
    assert not result.success
    assert result.suite_errors


def test_stryker_locations_use_javascript_utf16_columns():
    assert source_span(['const emoji = "😀"; return 1;\n'], 1, 21, 1, 27) == "return"


def test_corrupt_tool_reports_have_actionable_errors():
    import pytest

    from regress.errors import ToolError

    for malformed in (None, [], {"success": "false"}, {"success": True, "testResults": [None]}):
        with pytest.raises(ToolError, match="invalid report"):
            parse_vitest_report(malformed)
    for malformed in (None, {}, {"files": []}, {"files": {"src/cart.ts": {"mutants": []}}}):
        with pytest.raises(ToolError, match="invalid report"):
            parse_stryker_report(malformed, "src/cart.ts", 1)


def test_stryker_file_matching_requires_a_path_boundary():
    import pytest

    from regress.errors import ToolError

    with pytest.raises(ToolError, match="no results"):
        parse_stryker_report({"files": {"other-src/cart.ts": {"source": "", "mutants": []}}}, "src/cart.ts", 1)


def test_invalid_stryker_locations_are_rejected():
    import pytest

    from regress.errors import ToolError

    report = load_fixture("stryker-report.json")
    report["files"]["src/cart.ts"]["mutants"][0]["location"]["start"]["line"] = 0
    with pytest.raises(ToolError, match="invalid source location"):
        parse_stryker_report(report, "src/cart.ts", 1)
