"""`regress check`: scoring the existing tests without a model, the CI gate, and its outputs."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import EXAMPLES, requires_examples
from typer.testing import CliRunner

from regress.batch import Target
from regress.check import COMMENT_MARKER, CheckFile, CheckReport, Survivor, render_markdown, run_check, spread_survivors
from regress.cli import app
from regress.evaluation import sandbox
from regress.models import Mutant, MutantStatus, MutationRun


def _mutant(id: str, line: int, status: MutantStatus = MutantStatus.SURVIVED, column: int = 1) -> Mutant:
    return Mutant(
        id=id,
        mutator="M",
        status=status,
        replacement="b",
        original="a",
        start_line=line,
        start_column=column,
        end_line=line,
        end_column=column + 1,
    )


def _report(*files: CheckFile, fail_under: float | None = None) -> CheckReport:
    return CheckReport(
        id="c", created_at="2026-10-04T12:00:00+00:00", project_root="/p", fail_under=fail_under, files=list(files)
    )


def _scored(name: str, detected: int, valid: int) -> CheckFile:
    return CheckFile(
        source_file=name, status="scored", killed=detected, survived=valid - detected, score=100 * detected / valid
    )


def test_spread_survivors_covers_lines_before_repeating_one():
    run = MutationRun(
        index=1,
        mutants=[
            _mutant("1", 5, MutantStatus.NO_COVERAGE),
            _mutant("2", 5, column=9),
            _mutant("3", 5, column=12),
            _mutant("4", 9),
            _mutant("5", 2, MutantStatus.KILLED),
        ],
    )
    # One per line first, preferring a survivor over an uncovered mutant on the same line; killed ones never.
    assert [m.id for m in spread_survivors(run, 2)] == ["2", "4"]
    assert [m.id for m in spread_survivors(run, 10)] == ["1", "2", "3", "4"]


def test_combined_score_and_gate():
    a, b = _scored("a.ts", 9, 10), _scored("b.ts", 1, 10)
    assert _report(a, b).score == 50.0
    assert _report(a, b).passed  # no threshold: only errors fail
    assert _report(a, b, fail_under=50).passed
    assert not _report(a, b, fail_under=51).passed

    untested = CheckFile(source_file="c.ts", status="no-tests")
    assert _report(a, untested).passed
    assert not _report(a, untested, fail_under=10).passed  # a threshold means every file needs tests

    failed = CheckFile(source_file="d.ts", status="failed", error="boom")
    assert not _report(a, failed).passed
    skipped = CheckFile(source_file="e.ts", status="skipped")
    assert _report(a, skipped, fail_under=90).passed
    assert _report(fail_under=90).passed  # nothing changed, nothing to fail


def test_markdown_comment_has_marker_table_and_survivors():
    a = _scored("src/a.ts", 3, 4)
    a.mutate_lines = [(4, 9)]
    a.tests = 2
    a.survivors = [
        Survivor(id="7", mutator="EqualityOperator", status="Survived", line=5, original="x > 1", mutated="x >= 1")
    ]
    untested = CheckFile(source_file="src/b.ts", status="no-tests", error="No test file | none")

    text = render_markdown(_report(a, untested, fail_under=80))

    assert text.startswith(COMMENT_MARKER)
    assert "❌ failed (threshold 80%)" in text
    assert "| `src/a.ts` | 4-9 | 2 | 3/4 | 75% |  |" in text
    assert "| `src/b.ts` | all | — | — | — | no tests |" in text
    assert "No test file \\| none" in text  # cannot break the table
    assert "**EqualityOperator** · `src/a.ts:5` (survived)\n```diff\n- x > 1\n+ x >= 1\n```" in text


def test_check_needs_a_source_or_changed():
    result = CliRunner().invoke(app, ["check"])
    assert result.exit_code == 1
    assert "--changed" in result.output


@pytest.fixture
def root() -> Iterator[Path]:
    with sandbox(EXAMPLES) as path:
        yield path


@pytest.mark.integration
@requires_examples
def test_check_scores_existing_tests_without_a_model(root):
    (root / "test/slugify.test.ts").write_text(
        (root / "test/slugify.test.ts").read_text().replace("toBe(", "not.toBe(", 1)
    )
    targets = [
        Target(root / "src/cart.ts", [(36, 48)]),
        Target(root / "src/pagination.ts"),
        Target(root / "src/slugify.ts"),
        Target(root / "src/intervals.ts", [(2, 3)]),  # a blank line and a doc comment
    ]
    original_cart_tests = (root / "test/cart.test.ts").read_text()

    report, directory = run_check(root, targets, fail_under=10)

    cart, pagination, slugify, intervals = report.files
    assert cart.status == "scored" and cart.mutate_lines == [(37, 47)]
    assert cart.tests == 3 and cart.valid > 0 and cart.score is not None
    assert all(37 <= s.line <= 47 for s in cart.survivors)
    assert pagination.status == "no-tests" and pagination.test_file == "test/pagination.test.ts"
    assert slugify.status == "failed" and "do not pass" in slugify.error
    assert intervals.status == "skipped"
    assert not report.passed
    # Nothing in the project changed, and the report is saved with the tool artifacts.
    assert (root / "test/cart.test.ts").read_text() == original_cart_tests
    assert not (root / "test/pagination.test.ts").exists()
    assert CheckReport.model_validate_json((directory / "check.json").read_text()) == report
    assert (directory / "01" / "stryker" / "mutation-1.json").is_file()


@pytest.mark.integration
@requires_examples
def test_check_cli_prints_json_and_gates_on_the_threshold(root, monkeypatch):
    monkeypatch.chdir(root)
    comment = root / "comment.md"
    cli = CliRunner()

    passing = cli.invoke(app, ["check", "src/cart.ts", "--json", "--fail-under", "1", "--markdown", str(comment)])
    assert passing.exit_code == 0, passing.output
    data = json.loads(passing.stdout)
    assert data["fail_under"] == 1 and data["files"][0]["status"] == "scored"
    assert comment.read_text().startswith(COMMENT_MARKER)

    failing = cli.invoke(app, ["check", "src/cart.ts", "--fail-under", "99"])
    assert failing.exit_code == 1, failing.output
    assert "threshold 99%" in failing.output and "failed" in failing.output
