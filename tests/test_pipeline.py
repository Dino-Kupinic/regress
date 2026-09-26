"""End-to-end runs against real Vitest and Stryker, with a scripted model."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import EXAMPLES, ScriptedLLM, requires_examples
from typer.testing import CliRunner

from regress.cli import app
from regress.errors import CandidateRejected, RegressError
from regress.evaluation import evaluate_regress, hidden_bugs_caught, load_suites, sandbox
from regress.pipeline import Pipeline, RunOptions
from regress.project import load_project
from regress.ui import Reporter

pytestmark = [pytest.mark.integration, requires_examples]

BASELINE = (EXAMPLES / "test/cart.test.ts").read_text()
ORACLE = (EXAMPLES / "oracle/cart.oracle.ts").read_text()
ONE_SHOT = (
    BASELINE.rstrip().removesuffix("});")
    + """
  it("accumulates quantity when the same sku is added twice", () => {
    const cart = new Cart();
    cart.add("a", 1, 2);
    cart.add("a", 1, 3);
    expect(cart.itemCount).toBe(5);
  });

  it("applies a percent coupon", () => {
    const cart = new Cart();
    cart.add("a", 80);
    cart.applyCoupon({ code: "TEN", kind: "percent", amount: 10 });
    expect(cart.discount()).toBe(8);
  });
});
"""
)
WRONG_EXPECTATION = ONE_SHOT.replace("expect(cart.discount()).toBe(8);", "expect(cart.discount()).toBe(10);")
DROPS_BASELINE_TEST = ONE_SHOT.replace('it("rejects negative prices"', 'it("refuses negative prices"')
# A forgotten `await` in an async test: the test passes, the assertion fails later as an unhandled rejection.
UNAWAITED_ASSERTION = ONE_SHOT.replace(
    'it("applies a percent coupon", () => {', 'it("applies a percent coupon", async () => {'
).replace("expect(cart.discount()).toBe(8);", "expect(Promise.resolve(cart.discount())).resolves.toBe(10);")


@pytest.fixture
def root() -> Iterator[Path]:
    with sandbox(EXAMPLES) as path:
        yield path


def run(root: Path, source: str, llm: ScriptedLLM, **options):
    project = load_project(root / source)
    return project, Pipeline(project, llm, RunOptions(**options)).run()


def test_mutation_feedback_improves_score_and_keeps_best_tests(root):
    llm = ScriptedLLM(ONE_SHOT, ORACLE)
    project, report = run(root, "src/cart.ts", llm, measure_baseline=True)

    assert report.status == "completed"
    assert [s.kind for s in report.stages] == ["baseline", "generated", "improved"]
    baseline, generated, improved = (s.score for s in report.stages)
    assert baseline < generated < improved
    assert improved > 90
    assert report.kept_stage == "Improved tests"
    assert report.improvement == pytest.approx(improved - generated)
    assert project.test_file.read_text() == ORACLE

    # The improvement prompt carried concrete surviving mutants from the first run.
    assert "Mutants the current tests fail to detect" in llm.prompts[1]
    assert "original:" in llm.prompts[1] and "mutated:" in llm.prompts[1]

    run_dir = root / ".regress/runs" / report.id
    saved = json.loads((run_dir / "report.json").read_text())
    assert saved["status"] == "completed"
    assert (run_dir / report.stages[2].test_file_snapshot).read_text() == ORACLE
    assert len(list((run_dir / "llm").glob("*.prompt.md"))) == 2


def test_failing_candidate_is_repaired_with_the_error_fed_back(root):
    llm = ScriptedLLM(WRONG_EXPECTATION, ONE_SHOT)
    project, report = run(root, "src/cart.ts", llm, rounds=0)

    assert report.stage("generated").attempts == 2
    assert "Your previous attempt was rejected" in llm.prompts[1]
    assert "expected 8 to be 10" in llm.prompts[1]
    assert project.test_file.read_text() == ONE_SHOT


def test_unhandled_errors_are_repaired_even_when_every_test_passes(root):
    # Accepting this file would leave `vitest run` failing and crash Stryker's dry run.
    llm = ScriptedLLM(UNAWAITED_ASSERTION, ONE_SHOT)
    project, report = run(root, "src/cart.ts", llm, rounds=0)

    assert report.stage("generated").attempts == 2
    assert "unhandled promise rejection" in llm.prompts[1]
    assert "expected 8 to be 10" in llm.prompts[1]
    assert project.test_file.read_text() == ONE_SHOT


def test_removing_an_existing_test_is_never_accepted(root):
    llm = ScriptedLLM(DROPS_BASELINE_TEST, DROPS_BASELINE_TEST)
    project = load_project(root / "src/cart.ts")
    with pytest.raises(CandidateRejected) as error:
        Pipeline(project, llm, RunOptions(max_repairs=1)).run()

    assert any('"Cart rejects negative prices"' in p for p in error.value.problems)
    assert project.test_file.read_text() == BASELINE
    saved = json.loads(next((root / ".regress/runs").glob("*/report.json")).read_text())
    assert saved["status"] == "failed"


def test_rejected_improvement_keeps_the_previous_tests(root):
    llm = ScriptedLLM(ONE_SHOT, WRONG_EXPECTATION, WRONG_EXPECTATION)
    project, report = run(root, "src/cart.ts", llm, max_repairs=1)

    assert report.status == "completed"
    assert report.stages[-1].rejected
    assert report.kept_stage == "Generated tests"
    assert project.test_file.read_text() == ONE_SHOT


def test_creates_a_missing_test_file_and_removes_it_on_failure(root):
    oracle = (EXAMPLES / "oracle/pagination.oracle.ts").read_text()
    project, report = run(root, "src/pagination.ts", ScriptedLLM(oracle), rounds=0)
    assert not report.test_file_existed
    assert report.stage("baseline").test_count == 0
    assert project.test_file.read_text() == oracle

    project.test_file.unlink()
    broken = oracle.replace("totalPages: 3,", "totalPages: 4,")
    with pytest.raises(CandidateRejected):
        run(root, "src/pagination.ts", ScriptedLLM(broken), max_repairs=0)
    assert not project.test_file.exists()


def test_failing_existing_tests_stop_the_run(root):
    test_file = root / "test/cart.test.ts"
    test_file.write_text(BASELINE.replace("toBe(5)", "toBe(6)"))
    with pytest.raises(RegressError, match="existing tests .* do not pass"):
        run(root, "src/cart.ts", ScriptedLLM())
    assert "toBe(6)" in test_file.read_text()


def test_hidden_bugs_are_caught_by_the_oracle_but_not_the_baseline(root):
    suite = next(s for s in load_suites(root) if s.name == "intervals")
    project = load_project(root / suite.source)
    out = root / ".regress/hidden"
    weak = hidden_bugs_caught(project, suite, "Existing", project.test_file.read_text(), out)
    strong = hidden_bugs_caught(project, suite, "Oracle", (root / suite.oracle).read_text(), out)

    assert weak.caught == []
    assert sorted(strong.caught) == sorted(b.id for b in suite.bugs)
    assert project.source.read_text() == (EXAMPLES / suite.source).read_text()


def test_report_command_renders_saved_runs(root):
    run(root, "src/cart.ts", ScriptedLLM(ONE_SHOT, ORACLE))
    cli = CliRunner()

    result = cli.invoke(app, ["report", "--project", str(root)])
    assert result.exit_code == 0, result.output
    assert "Improved tests ✓ kept" in result.output
    assert "Undetected mutants" in result.output

    listing = cli.invoke(app, ["report", "--list", "--project", str(root)])
    assert "src/cart.ts" in listing.output

    as_json = cli.invoke(app, ["report", "--json", "--project", str(root)])
    assert json.loads(as_json.output)["source_file"] == "src/cart.ts"


def test_eval_counts_a_module_the_model_failed_as_its_existing_tests(root):
    # Leaving the module out would total only the modules the model managed, flattering both AI columns.
    suite = next(s for s in load_suites(root) if s.name == "cart")
    llm = ScriptedLLM(WRONG_EXPECTATION)
    result = evaluate_regress(root, [suite], lambda: llm, RunOptions(max_repairs=0), Reporter())

    [module] = result.modules
    assert module.error is None
    assert "did not produce valid tests" in module.note
    existing, one_shot, regress = module.stages
    assert [s.label for s in module.stages] == ["Existing tests", "One-shot AI", "Regress"]
    assert set(existing.missed) == {b.id for b in suite.bugs}  # the weak existing tests catch none
    assert existing.score is not None and existing.score > 0
    for ai in (one_shot, regress):
        assert ai.model_dump(exclude={"label"}) == existing.model_dump(exclude={"label"})
    assert result.counted_modules == ["cart"]
    assert result.totals[1].bugs_total == len(suite.bugs)
