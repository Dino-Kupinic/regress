"""Total allowances span files, repair attempts and subprocess workers without losing results."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import ScriptedFactory, ScriptedLLM
from fastapi.testclient import TestClient
from rich.text import Text
from typer.testing import CliRunner

from regress.api import create_app
from regress.batch import BatchReport, Target, run_batch
from regress.budget import TotalBudget
from regress.cli import app
from regress.config import load_settings
from regress.errors import ProjectError
from regress.estimates import run_options
from regress.evaluation import BugSuite, EvalResult, StageResult, evaluate_regress
from regress.models import Mutant, MutationRun, TestCase, TestRunResult, TestStatus
from regress.pipeline import Pipeline, RunOptions
from regress.project import load_project
from regress.ui import Reporter

PRICE = {"input": 1000.0, "output": 6000.0}  # ScriptedLLM spends $0.40 per response.


@pytest.fixture
def project(js_project, monkeypatch):
    (js_project / "src/a.ts").write_text("export const x = 1;\n")
    (js_project / "src/b.ts").write_text("export const y = 2;\n")
    (js_project / "test").mkdir()
    (js_project / "test/a.test.ts").write_text("original a\r\n")
    (js_project / "test/b.test.ts").write_text("original b\r\n")
    monkeypatch.setattr("regress.pipeline.next_call_cost", lambda *args: 0.6)
    passed = TestRunResult(
        success=True, tests=[TestCase(name="original", full_name="original", status=TestStatus.PASSED)]
    )
    monkeypatch.setattr("regress.pipeline.run_vitest", lambda *args: passed)
    mutant = Mutant(
        id="1",
        mutator="M",
        status="Survived",
        original="1",
        replacement="0",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=2,
    )
    monkeypatch.setattr("regress.pipeline.run_stryker", lambda *args: MutationRun(index=1, mutants=[mutant]))

    def check(project, workspace, content, **kwargs):
        workspace.write_tests(content)
        return SimpleNamespace(ok=True, result=passed, problems=[])

    monkeypatch.setattr("regress.pipeline.check_candidate", check)
    return load_project(js_project / "src/a.ts")


def options(limit=1.0, **kwargs):
    from regress.pricing import Price

    return RunOptions(price=Price(**PRICE), total_budget=TotalBudget(limit), **kwargs)


def test_allowance_is_shared_across_files_and_keeps_the_first_result(project):
    llm = ScriptedLLM("accepted a", "must not be called")
    settings = options(0.9, rounds=0)
    report = run_batch(project.root, [Target(project.source), Target(project.root / "src/b.ts")], llm, settings)
    assert len(llm.prompts) == 1
    assert report.cost_usd == pytest.approx(0.4)
    assert report.max_total_cost == 0.9 and report.stop_reason
    # The second file keeps its existing tests and stops before generation; no fake improved score.
    assert project.test_file.read_text() == "accepted a\n"
    assert (project.root / "test/b.test.ts").read_bytes() == b"original b\r\n"
    assert report.files[1].status == "skipped" and report.files[1].error == report.stop_reason
    assert report.files[1].tests_first == report.files[1].tests_kept == 1
    assert report.files[1].score_kept is None
    saved = BatchReport.model_validate_json((project.root / ".regress/batches" / f"{report.id}.json").read_text())
    assert saved.cost_usd == pytest.approx(0.4) and saved.stop_reason


def test_repair_attempts_share_the_allowance_and_restore_existing_bytes(project, monkeypatch):
    def reject(project, workspace, content, **kwargs):
        workspace.write_tests(content)
        return SimpleNamespace(ok=False, result=None, problems=["bad candidate"])

    monkeypatch.setattr("regress.pipeline.check_candidate", reject)
    llm = ScriptedLLM("bad one", "bad repair", "must not be called")
    pipeline = Pipeline(project, llm, options(1.0, rounds=0))
    report = pipeline.run()
    assert len(llm.prompts) == 2 and report.cost_usd == pytest.approx(0.8)
    assert report.total_budget_stop and report.kept_stage == "Existing tests"
    assert project.test_file.read_bytes() == b"original a\r\n"


def test_budget_stops_improvement_and_keeps_valid_generation(project):
    llm = ScriptedLLM("accepted generation", "must not be called")
    report = Pipeline(project, llm, options(0.9)).run()
    assert len(llm.prompts) == 1 and report.kept_stage == "Generated tests"
    assert report.stop_reason and report.total_budget_stop
    assert project.test_file.read_text() == "accepted generation\n"


def test_overrun_skips_unstarted_files_and_records_reason(project):
    llm = ScriptedLLM("accepted generation")
    from regress.pricing import Price

    settings = options(0.7, rounds=0)
    settings.price = Price(input=3000.0, output=10000.0)  # $0.80 actual, $0.60 estimated
    report = run_batch(project.root, [Target(project.source), Target(project.root / "src/b.ts")], llm, settings)
    assert [f.status for f in report.files] == ["completed", "skipped"]
    assert report.files[1].run_id is None and report.files[1].error == report.stop_reason
    assert report.cost_usd == pytest.approx(0.8)
    assert project.test_file.read_text() == "accepted generation\n"


def test_failed_candidates_still_count_toward_next_files_allowance(project, monkeypatch):
    monkeypatch.setattr(
        "regress.pipeline.check_candidate",
        lambda *args, **kwargs: SimpleNamespace(ok=False, result=None, problems=["bad candidate"]),
    )
    llm = ScriptedLLM("bad candidate", "must not be called")
    settings = options(0.9, rounds=0, max_repairs=0)
    report = run_batch(project.root, [Target(project.source), Target(project.root / "src/b.ts")], llm, settings)
    assert report.files[0].status == "failed" and report.files[0].cost_usd == pytest.approx(0.4)
    assert report.cost_usd == pytest.approx(0.4) and len(llm.prompts) == 1 and report.stop_reason


def test_per_file_budget_still_resets_and_does_not_stop_other_files(project):
    settings = options(10.0, max_cost=0.9, rounds=1)
    llm = ScriptedLLM("accepted a", "accepted b")
    report = run_batch(project.root, [Target(project.source), Target(project.root / "src/b.ts")], llm, settings)
    assert len(llm.prompts) == 2 and all(f.status == "completed" for f in report.files)
    assert report.cost_usd == pytest.approx(0.8) and report.stop_reason is None


def test_evaluation_aggregate_includes_failed_modules_and_skips_remaining(project, monkeypatch):
    settings = options(0.7, rounds=0, max_repairs=0)
    from regress.pricing import Price

    settings.price = Price(input=3000.0, output=10000.0)
    monkeypatch.setattr(
        "regress.pipeline.check_candidate",
        lambda *args, **kwargs: SimpleNamespace(ok=False, result=None, problems=["bad candidate"]),
    )
    monkeypatch.setattr(
        "regress.evaluation.hidden_bugs_caught", lambda project, suite, label, *args: StageResult(label=label)
    )
    suites = [BugSuite(name=name, source=f"src/{name}.ts", test=f"test/{name}.test.ts", bugs=[]) for name in ("a", "b")]
    llm = ScriptedLLM("bad candidate")
    result = evaluate_regress(project.root, suites, lambda: llm, settings, Reporter())
    assert len(llm.prompts) == 1 and result.modules[1].skipped and not result.modules[1].stages
    assert result.cost_usd == pytest.approx(0.8) and result.stop_reason and result.max_total_cost == 0.7
    assert project.test_file.read_bytes() == b"original a\r\n"  # sandboxed
    saved = EvalResult.model_validate_json((Path(result.output_dir) / "eval.json").read_text())
    assert saved.cost_usd == pytest.approx(0.8) and saved.modules[1].skipped


@pytest.mark.parametrize("limit", [0, -1, float("inf"), float("nan"), 10001])
def test_total_budget_rejects_invalid_values(limit):
    with pytest.raises(ProjectError):
        TotalBudget(limit)


def test_unknown_prices_are_refused_before_a_run(js_project):
    settings = load_settings(js_project, model="openai-compatible:unknown")
    with pytest.raises(ProjectError, match="max_total_cost needs a price"):
        run_options(settings, settings.model, max_total_cost=1)
    with TestClient(create_app(js_project, llm_factory=ScriptedFactory()), base_url="http://127.0.0.1") as client:
        data = {"sources": ["src/a.ts"], "model": "openai-compatible:unknown", "max_total_cost": 1}
        assert client.post("/api/batches", json=data).status_code == 400
        assert client.post("/api/estimate", json=data).status_code == 400
        assert client.get("/api/project").json()["active_batch"] is None


@pytest.mark.parametrize("total", ["1", "0.00001"])
@pytest.mark.parametrize("colored", [False, True])
def test_cli_exposes_total_budget_and_oracle_rejects_it(js_project, total, colored, monkeypatch):
    # CI forces Rich's terminal formatting; ANSI codes can split an option's two leading dashes.
    monkeypatch.setattr("typer.rich_utils.FORCE_TERMINAL", colored)
    monkeypatch.setattr("typer.rich_utils.COLOR_SYSTEM", "standard" if colored else None)
    monkeypatch.delenv("NO_COLOR", raising=False)
    runner = CliRunner()
    for command in ("run", "eval"):
        result = runner.invoke(app, [command, "--help"], color=colored)
        assert result.exit_code == 0 and "--max-total-cost" in Text.from_ansi(result.output).plain
        if colored:
            assert "\x1b[" in result.output
    (js_project / "hidden-bugs").mkdir()
    result = runner.invoke(app, ["eval", str(js_project), "--oracle", "--max-total-cost", total])
    assert result.exit_code == 1 and "not --oracle" in result.output


def test_generation_blocked_before_any_call_does_not_create_missing_tests(project):
    project.test_file.unlink()
    llm = ScriptedLLM()
    report = Pipeline(project, llm, options(0.5)).run()
    assert report.total_budget_stop and report.usage.calls == 0
    assert not project.test_file.exists() and report.cost_usd == 0


def test_evaluation_failed_validation_spend_is_in_total_even_without_comparable_results(project, monkeypatch):
    from regress.errors import RegressError

    def fail_validation(*args, **kwargs):
        raise RegressError("validation tool unavailable")

    monkeypatch.setattr("regress.pipeline.check_candidate", fail_validation)
    monkeypatch.setattr(
        "regress.evaluation.hidden_bugs_caught",
        lambda *args: pytest.fail("Failed and skipped modules must not be evaluated as successful AI stages"),
    )
    suites = [BugSuite(name=name, source=f"src/{name}.ts", test=f"test/{name}.test.ts", bugs=[]) for name in ("a", "b")]
    llm = ScriptedLLM("candidate")
    result = evaluate_regress(project.root, suites, lambda: llm, options(0.9, rounds=0), Reporter())
    assert len(llm.prompts) == 1 and result.modules[0].error and result.modules[1].skipped
    assert result.cost is None and result.cost_usd == pytest.approx(0.4)
    assert result.modules[0].cost_usd == pytest.approx(0.4) and result.stop_reason
