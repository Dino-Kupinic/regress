"""Cache invalidation and parity with full mutation testing."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import EXAMPLES, load_fixture, requires_examples
from rich.console import Console

from regress import stryker
from regress.config import Settings, load_settings
from regress.errors import ToolError
from regress.estimates import run_options
from regress.models import MutantStatus, MutationRun, Stage
from regress.project import Project, Toolchain, load_project
from regress.stryker import run_stryker
from regress.ui import ConsoleReporter


@pytest.fixture
def project(tmp_path: Path) -> Project:
    source = tmp_path / "src/cart.ts"
    source.parent.mkdir()
    source.write_text(load_fixture("stryker-report.json")["files"]["src/cart.ts"]["source"])
    test_file = tmp_path / "test/cart.test.ts"
    test_file.parent.mkdir()
    test_file.write_text("// tests\n")
    return Project(tmp_path, source, test_file, Toolchain("bun", "4.1.0", "10.0.0", "10.0.0"))


@pytest.fixture
def tool(monkeypatch):
    calls = []

    def run(args, cwd, timeout, log_path):
        config = json.loads((cwd / args[-1]).read_text())
        cache = cwd / config["incrementalFile"] if config["incremental"] else None
        previous = json.loads(cache.read_text()) if cache and cache.exists() else None
        calls.append((config, previous))
        report = load_fixture("stryker-report.json")
        (cwd / config["jsonReporter"]["fileName"]).write_text(json.dumps(report))
        if cache:
            cache.write_text(json.dumps(report))
        count = len(report["files"]["src/cart.ts"]["mutants"])
        output = (
            f"\x1b[33m3\x1b[0m of {count} mutant result(s) are reused." if previous else "No incremental result file"
        )
        return SimpleNamespace(returncode=0, output=output, duration_seconds=1.2)

    monkeypatch.setattr("regress.stryker.run_command", run)
    return calls


def test_reuses_cache_and_records_counts(project, tool):
    output = project.root / ".regress/run/stryker"
    first = run_stryker(project, output, 1)
    second = run_stryker(project, output, 2)
    assert first.reused_mutants == 0
    assert second.reused_mutants == 3
    assert second.retested_mutants == len(second.mutants) - 3
    assert second.model_dump()["reused_mutants"] == 3
    assert tool[0][0]["incrementalFile"] == ".regress/run/stryker/incremental.json"
    assert tool[0][1] is None and tool[1][1] is not None


@pytest.mark.parametrize(
    "change",
    ["source", "source_bytes", "scope", "test_path", "toolchain", "new_run", "directory", "corrupt", "interrupted"],
)
def test_invalidates_cache(project, tool, change):
    output = project.root / ".regress/run/stryker"
    run_stryker(project, output, 1)
    index = 2
    if change == "source":
        project.source.write_text(project.source.read_text() + "// edit\n")
    elif change == "source_bytes":
        project.source.write_bytes(project.source.read_bytes().replace(b"\n", b"\r\n"))
    elif change == "scope":
        project = replace(project, mutate_lines=((4, 4),))
    elif change == "test_path":
        other = project.test_file.with_name("other.test.ts")
        other.write_bytes(project.test_file.read_bytes())
        project = replace(project, test_file=other)
    elif change == "toolchain":
        project = replace(project, toolchain=replace(project.toolchain, stryker="10.1.0"))
    elif change == "new_run":
        index = 1
    elif change == "directory":
        output = output.parent / "another/stryker"
    elif change == "corrupt":
        (output / "incremental.json").write_text("{broken")
    elif change == "interrupted":
        (output / "incremental-context.json").unlink()
    result = run_stryker(project, output, index)
    assert tool[-1][1] is None
    assert result.reused_mutants == 0


def test_static_mutants_are_retested_after_test_edits(project, tool):
    output = project.root / ".regress/run/stryker"
    run_stryker(project, output, 1)
    cache = output / "incremental.json"
    report = json.loads(cache.read_text())
    mutants = report["files"]["src/cart.ts"]["mutants"]
    mutants[0]["static"] = True
    project.test_file.write_text("// new tests\n")
    cache.write_text(json.dumps(report))
    run_stryker(project, output, 2)
    retained = tool[-1][1]["files"]["src/cart.ts"]["mutants"]
    assert mutants[0]["id"] not in {m["id"] for m in retained}
    assert all(m["static"] is False for m in retained)


def test_failed_tool_cannot_seed_next_round(project, tool, monkeypatch):
    output = project.root / ".regress/run/stryker"
    run_stryker(project, output, 1)
    fake_run = stryker.run_command

    def fail(*args, **kwargs):
        result = fake_run(*args, **kwargs)
        result.returncode = 1
        return result

    monkeypatch.setattr("regress.stryker.run_command", fail)
    with pytest.raises(ToolError):
        run_stryker(project, output, 2)
    monkeypatch.setattr("regress.stryker.run_command", fake_run)
    run_stryker(project, output, 3)
    assert tool[-1][1] is None


def test_config_can_disable_incremental(project, tool):
    (project.root / "regress.toml").write_text("incremental = false\n")
    settings = load_settings(project.root)
    assert Settings().incremental
    assert not run_options(settings, settings.model).incremental
    result = run_stryker(project, project.root / ".regress/run/stryker", 1, incremental=settings.incremental)
    assert tool[-1][0]["incremental"] is False
    assert "incrementalFile" not in tool[-1][0]
    assert result.reused_mutants is None and result.retested_mutants is None


def test_old_reports_and_terminal_counts():
    old = MutationRun.model_validate({"index": 1, "mutants": []})
    assert old.reused_mutants is None and old.retested_mutants is None
    console = Console(record=True)
    ConsoleReporter(console).mutation(
        Stage(kind="generated", label="Generated", mutation=MutationRun(index=2, mutants=[], reused_mutants=0))
    )
    output = console.export_text()
    assert "Reused" in output and "Re-tested" in output


@pytest.mark.integration
@requires_examples
def test_new_tests_kill_previous_survivors_and_match_full_run(tmp_path):
    (tmp_path / "package.json").write_text('{"type":"module"}')
    (tmp_path / "node_modules").symlink_to(EXAMPLES / "node_modules", target_is_directory=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "test").mkdir()
    source = tmp_path / "src/classify.ts"
    source.write_text(
        'export const marker = "ready";\nexport function classify(n: number) { return n > 0 ? "positive" : "other"; }\n'
    )
    test_file = tmp_path / "test/classify.test.ts"
    baseline = 'import { expect, it } from "vitest";\nimport { classify, marker } from "../src/classify";\nit("negative", () => { expect(classify(-1)).toBe("other"); });\nit("zero", () => { expect(classify(0)).toBe("other"); });\n'
    test_file.write_text(baseline)
    project = load_project(source)
    output = tmp_path / ".regress/run/stryker"
    first = run_stryker(project, output, 1)
    test_file.write_text(
        baseline
        + 'it("positive", () => { expect(classify(1)).toBe("positive"); });\nit("marker", () => { expect(marker).toBe("ready"); });\n'
    )
    incremental = run_stryker(project, output, 2)
    full = run_stryker(project, tmp_path / ".regress/full/stryker", 1, incremental=False)

    def statuses(run):
        return {m.id: m.status for m in run.mutants}

    assert statuses(incremental) == statuses(full)
    assert incremental.score == full.score > first.score
    assert incremental.reused_mutants > 0
    assert any(m.undetected and statuses(incremental)[m.id] == MutantStatus.KILLED for m in first.mutants)
    raw = json.loads((output / "mutation-1.json").read_text())
    assert all(t.get("location") for t in raw["testFiles"][project.test_rel]["tests"])
    assert any(m["static"] for m in raw["files"][project.source_rel]["mutants"])
