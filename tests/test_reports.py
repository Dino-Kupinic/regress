from conftest import load_fixture

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

