from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from regress.errors import RegressError, ToolError
from regress.files import atomic_write_text
from regress.models import TestCase, TestRunResult, TestStatus
from regress.pipeline import Pipeline, RunOptions
from regress.project import Project, Toolchain
from regress.store import RunStore
from regress.validate import Workspace, check_candidate


@pytest.fixture
def project(tmp_path: Path) -> Project:
    source = tmp_path / "src/cart.ts"
    source.parent.mkdir()
    source.write_bytes(b"export const x = 1;\r\n")
    tests = tmp_path / "test/cart.test.ts"
    tests.parent.mkdir()
    tests.write_bytes(b'import { x } from "../src/cart";\r\n')
    return Project(tmp_path, source, tests, Toolchain("bun", "4.1.0", "10.0.0", "10.0.0"))


def test_failed_atomic_write_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    path.write_text("previous")

    def fail(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr("regress.files.os.replace", fail)
    with pytest.raises(OSError, match="disk unavailable"):
        atomic_write_text(path, "new report")
    assert path.read_text() == "previous"
    assert list(tmp_path.iterdir()) == [path]


def test_atomic_write_preserves_existing_permissions(tmp_path):
    path = tmp_path / "test.ts"
    path.write_text("old")
    path.chmod(0o640)
    atomic_write_text(path, "new")
    assert path.stat().st_mode & 0o777 == 0o640


def test_concurrent_runs_allocate_distinct_directories(tmp_path):
    store = RunStore(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        directories = list(pool.map(store.create, [tmp_path / "cart.ts"] * 32))
    assert len(set(directories)) == 32
    assert all(path.is_dir() for path in directories)


def test_corrupt_saved_report_is_a_domain_error(tmp_path):
    store = RunStore(tmp_path)
    run_dir = store.create(tmp_path / "cart.ts")
    (run_dir / "report.json").write_text('{"status":')
    with pytest.raises(RegressError, match="unreadable"):
        store.load()


def test_run_listing_does_not_follow_symlinks(tmp_path):
    store = RunStore(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "report.json").write_text("{}")
    store.base.mkdir(parents=True)
    (store.base / "linked").symlink_to(outside, target_is_directory=True)
    assert store.run_dirs() == []


@pytest.mark.parametrize("error", [ToolError("bad report"), KeyboardInterrupt()])
def test_pipeline_restores_source_and_exact_test_bytes_on_tool_failure(project, monkeypatch, error):
    from conftest import ScriptedLLM

    original_source = project.source.read_bytes()
    original_tests = project.test_file.read_bytes()

    def broken(*args, **kwargs):
        project.source.write_text("changed")
        project.test_file.write_text("changed")
        raise error

    monkeypatch.setattr("regress.pipeline.run_vitest", broken)
    with pytest.raises(type(error)):
        Pipeline(project, ScriptedLLM(), RunOptions()).run()
    assert project.source.read_bytes() == original_source
    assert project.test_file.read_bytes() == original_tests
    report, _ = RunStore(project.root).load()
    assert report.status == ("cancelled" if isinstance(error, KeyboardInterrupt) else "failed")


def test_source_changes_in_successful_baseline_are_rejected_and_restored(project, monkeypatch):
    from conftest import ScriptedLLM

    original = project.source.read_bytes()

    def modifies(*args, **kwargs):
        project.source.write_text("changed")
        return TestRunResult(success=True, tests=[TestCase(full_name="x", status=TestStatus.PASSED)])

    monkeypatch.setattr("regress.pipeline.run_vitest", modifies)
    with pytest.raises(RegressError, match="changed the source"):
        Pipeline(project, ScriptedLLM()).run()
    assert project.source.read_bytes() == original


def test_empty_failing_baseline_does_not_proceed_to_llm(project, monkeypatch):
    from conftest import ScriptedLLM

    llm = ScriptedLLM()
    monkeypatch.setattr(
        "regress.pipeline.run_vitest",
        lambda *a, **k: TestRunResult(success=False, suite_errors=["compile failure"]),
    )
    with pytest.raises(RegressError, match="do not pass"):
        Pipeline(project, llm).run()
    assert llm.prompts == []


def test_candidate_tool_error_restores_source(project, monkeypatch, tmp_path):
    workspace = Workspace(project)
    original = project.source.read_bytes()

    def broken(*args, **kwargs):
        project.source.unlink()
        raise ToolError("bad report")

    monkeypatch.setattr("regress.validate.run_vitest", broken)
    with pytest.raises(ToolError, match="bad report"):
        check_candidate(
            project,
            workspace,
            'import { x } from "../src/cart";',
            previous=None,
            required_tests=set(),
            min_tests=1,
            output_dir=tmp_path,
            name="candidate",
        )
    assert project.source.read_bytes() == original


def test_unsuccessful_candidate_cannot_pass_without_failure_details(project, monkeypatch, tmp_path):
    workspace = Workspace(project)
    monkeypatch.setattr(
        "regress.validate.run_vitest",
        lambda *a, **k: TestRunResult(success=False, tests=[TestCase(full_name="x", status=TestStatus.PASSED)]),
    )
    check = check_candidate(
        project,
        workspace,
        'import { x } from "../src/cart";',
        previous=None,
        required_tests=set(),
        min_tests=1,
        output_dir=tmp_path,
        name="candidate",
    )
    assert not check.ok
    assert any("unsuccessful" in problem for problem in check.problems)


def test_report_persistence_failure_rolls_back_test_changes(project, monkeypatch):
    from conftest import ScriptedLLM

    from regress.models import Stage
    from regress.pipeline import Version

    original = project.test_file.read_bytes()
    pipeline = Pipeline(project, ScriptedLLM())
    stage = Stage(kind="generated", label="Generated tests")
    monkeypatch.setattr(pipeline, "_run", lambda: Version(stage, "new tests\n"))

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(pipeline.store, "save", fail)
    with pytest.raises(OSError, match="disk full"):
        pipeline.run()
    assert project.test_file.read_bytes() == original


def test_invalid_evaluation_suite_paths_are_rejected(tmp_path):
    import json

    from regress.evaluation import load_suites

    directory = tmp_path / "hidden-bugs"
    directory.mkdir()
    (directory / "cart.json").write_text(json.dumps({"source": "../outside.ts", "test": "test.ts", "bugs": []}))
    with pytest.raises(RegressError, match="Invalid hidden bug suite"):
        load_suites(tmp_path)


def test_oracle_evaluation_saves_partial_results_on_interruption(project, monkeypatch):
    from contextlib import nullcontext

    from regress.evaluation import BugSuite, EvalResult, evaluate_oracles

    (project.root / "oracle.ts").write_text("oracle")
    suite = BugSuite(name="cart", source="src/cart.ts", test="test/cart.test.ts", oracle="oracle.ts", bugs=[])
    monkeypatch.setattr("regress.evaluation.sandbox", lambda root: nullcontext(root))
    monkeypatch.setattr("regress.evaluation.load_project", lambda *args: project)

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr("regress.evaluation.hidden_bugs_caught", interrupt)
    with pytest.raises(KeyboardInterrupt):
        evaluate_oracles(project.root, [suite])
    reports = list((project.root / ".regress/eval").glob("*/eval.json"))
    assert len(reports) == 1
    saved = EvalResult.model_validate_json(reports[0].read_text())
    assert [module.module for module in saved.modules] == ["cart"]


def test_evaluation_output_directories_are_unique(tmp_path):
    from regress.evaluation import _output_dir

    assert _output_dir(tmp_path) != _output_dir(tmp_path)


@pytest.mark.parametrize("component", [".regress", ".regress/runs"])
def test_artifacts_cannot_write_through_directory_symlinks(tmp_path, component):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / component
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RegressError, match="outside the project"):
        RunStore(root).create(root / "cart.ts")
    assert list(outside.iterdir()) == []
