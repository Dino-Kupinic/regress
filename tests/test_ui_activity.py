"""Console output: progress stays visible through pipes and log collectors, and paths print verbatim."""

from __future__ import annotations

import io
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console

from regress import ui
from regress.models import RunReport
from regress.project import Project, Toolchain


def test_nonterminal_activity_reports_elapsed_and_quiet_time(monkeypatch):
    monkeypatch.setattr(ui, "ACTIVITY_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(ui, "QUIET_AFTER_SECONDS", 0.001)
    output = io.StringIO()
    reporter = ui.ConsoleReporter(Console(file=output, force_terminal=False, width=120))

    with reporter.activity("Generating tests with gpt-test") as status:
        status("thinking")
        time.sleep(0.04)

    text = output.getvalue()
    assert "Generating tests with gpt-test..." in text
    assert "Still generating tests with gpt-test" in text
    assert "thinking" in text
    assert "quiet for" in text
    assert "elapsed" in text


def test_nonterminal_activity_stops_reporting_after_completion(monkeypatch):
    monkeypatch.setattr(ui, "ACTIVITY_HEARTBEAT_SECONDS", 0.01)
    output = io.StringIO()
    reporter = ui.ConsoleReporter(Console(file=output, force_terminal=False))

    with reporter.activity("Running existing tests"):
        pass
    completed = output.getvalue()
    time.sleep(0.03)

    assert output.getvalue() == completed


def test_paths_with_brackets_are_not_read_as_markup(tmp_path: Path):
    # Route folders like Next.js's app/[id]/ are common; Rich would swallow "[id]" as a style tag.
    project = Project(
        root=tmp_path,
        source=tmp_path / "app/[id]/page.ts",
        test_file=tmp_path / "app/[id]/page.test.ts",
        toolchain=Toolchain("bun", "4.1.0", "10.0.0", "10.0.0"),
    )
    report = RunReport(
        id="20260926-120000-page",
        created_at=datetime.now(),
        project_root=str(tmp_path),
        source_file=project.source_rel,
        test_file=project.test_rel,
        test_file_existed=False,
        model="gpt-test",
    )
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, width=200)

    ui.ConsoleReporter(console).start(project, report)
    ui.render_run_list([(report, tmp_path)], console)

    text = output.getvalue()
    assert "app/[id]/page.test.ts (will be created)" in text
    assert "app/[id]/page.ts" in text
