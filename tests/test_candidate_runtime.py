from pathlib import Path

import pytest

from regress.models import TestCase, TestRunResult, TestStatus
from regress.project import Project, Toolchain
from regress.validate import Workspace, check_candidate

CONTENT = 'import { x } from "../src/cart";\n'


@pytest.fixture
def project(tmp_path: Path) -> Project:
    source = tmp_path / "src/cart.ts"
    source.parent.mkdir()
    source.write_text("export const x = 1;\n")
    return Project(tmp_path, source, tmp_path / "test/cart.test.ts", Toolchain("bun", "4", "10", "10"))


def result(*tests: tuple[str, TestStatus]) -> TestRunResult:
    return TestRunResult(success=True, tests=[TestCase(full_name=name, status=status) for name, status in tests])


def check(project, monkeypatch, previous, candidate, **kwargs):
    monkeypatch.setattr("regress.validate.run_vitest", lambda *args: candidate)
    return check_candidate(
        project,
        Workspace(project),
        CONTENT,
        previous=CONTENT if previous is not None else None,
        required_tests=previous.names if previous is not None else set(),
        min_tests=previous.total + 1 if previous is not None else 1,
        output_dir=project.root / "output",
        name="candidate",
        previous_result=previous,
        **kwargs,
    )


@pytest.mark.parametrize("status", [TestStatus.SKIPPED, TestStatus.PENDING, TestStatus.TODO])
def test_runtime_skips_cannot_replace_an_existing_passing_test(project, monkeypatch, status):
    previous = result(("existing", TestStatus.PASSED))
    candidate = result(("existing", status), ("new", TestStatus.PASSED))

    checked = check(project, monkeypatch, previous, candidate)

    assert not checked.ok
    assert any(
        "existing passing tests no longer pass" in problem and '"existing"' in problem for problem in checked.problems
    )


@pytest.mark.parametrize("status", [TestStatus.SKIPPED, TestStatus.PENDING, TestStatus.TODO])
def test_new_skipped_tests_are_rejected_even_when_another_new_test_passes(project, monkeypatch, status):
    previous = result(("existing", TestStatus.PASSED))
    candidate = result(("existing", TestStatus.PASSED), ("new", TestStatus.PASSED), ("not run", status))

    checked = check(project, monkeypatch, previous, candidate)

    assert not checked.ok
    assert any("newly skipped" in problem and '"not run"' in problem for problem in checked.problems)


def test_a_fully_skipped_initial_candidate_is_rejected(project, monkeypatch):
    checked = check(project, monkeypatch, None, result(("not run", TestStatus.PENDING)))

    assert not checked.ok
    assert any("newly skipped" in problem for problem in checked.problems)


def test_preexisting_skips_can_be_preserved_alongside_new_passing_tests(project, monkeypatch):
    previous = result(("existing", TestStatus.PASSED), ("legacy skip", TestStatus.PENDING))
    candidate = result(("existing", TestStatus.PASSED), ("legacy skip", TestStatus.PENDING), ("new", TestStatus.PASSED))

    assert check(project, monkeypatch, previous, candidate).ok


def test_preexisting_skip_does_not_authorize_a_different_skip(project, monkeypatch):
    previous = result(("existing", TestStatus.PASSED), ("legacy skip", TestStatus.PENDING))
    candidate = result(("existing", TestStatus.PENDING), ("legacy skip", TestStatus.PASSED), ("new", TestStatus.PASSED))

    checked = check(project, monkeypatch, previous, candidate)

    assert not checked.ok
    assert any("newly skipped" in problem and '"existing"' in problem for problem in checked.problems)


def test_duplicate_passing_test_names_preserve_every_occurrence(project, monkeypatch):
    previous = result(("duplicate", TestStatus.PASSED), ("duplicate", TestStatus.PASSED))
    candidate = result(("duplicate", TestStatus.PASSED), ("new one", TestStatus.PASSED), ("new two", TestStatus.PASSED))

    checked = check(project, monkeypatch, previous, candidate)

    assert not checked.ok
    assert any("every occurrence active" in problem and '"duplicate"' in problem for problem in checked.problems)


def test_matching_a_legacy_skipped_name_does_not_allow_more_skips(project, monkeypatch):
    previous = result(("legacy skip", TestStatus.PENDING))
    candidate = result(
        ("legacy skip", TestStatus.PENDING), ("legacy skip", TestStatus.PENDING), ("new", TestStatus.PASSED)
    )

    checked = check(project, monkeypatch, previous, candidate)

    assert not checked.ok
    assert any("newly skipped" in problem for problem in checked.problems)


@pytest.mark.parametrize("measure_baseline", [False, True])
def test_no_generate_requires_a_passing_baseline_test(project, monkeypatch, measure_baseline):
    from conftest import ScriptedLLM

    from regress.errors import RegressError
    from regress.pipeline import Pipeline, RunOptions

    project.test_file.parent.mkdir()
    project.test_file.write_text(CONTENT)
    monkeypatch.setattr("regress.pipeline.run_vitest", lambda *args: result(("skipped", TestStatus.PENDING)))
    llm = ScriptedLLM()

    with pytest.raises(RegressError, match="at least one passing test"):
        Pipeline(project, llm, RunOptions(generate=False, measure_baseline=measure_baseline)).run()

    assert llm.prompts == []
    assert project.test_file.read_text() == CONTENT


def test_pipeline_preserves_baseline_runtime_statuses_for_candidate_validation(project, monkeypatch):
    from conftest import ScriptedLLM

    from regress.models import MutationRun
    from regress.pipeline import Pipeline, RunOptions

    project.test_file.parent.mkdir()
    project.test_file.write_text(CONTENT)
    baseline = result(("existing", TestStatus.PASSED), ("legacy skip", TestStatus.PENDING))
    candidate = result(("existing", TestStatus.PASSED), ("legacy skip", TestStatus.PENDING), ("new", TestStatus.PASSED))
    monkeypatch.setattr("regress.pipeline.run_vitest", lambda *args: baseline)
    monkeypatch.setattr("regress.validate.run_vitest", lambda *args: candidate)
    monkeypatch.setattr("regress.pipeline.run_stryker", lambda *args: MutationRun(index=1, mutants=[]))

    report = Pipeline(project, ScriptedLLM(CONTENT), RunOptions(rounds=0)).run()

    assert report.status == "completed"
    assert report.kept_stage == "Generated tests"
