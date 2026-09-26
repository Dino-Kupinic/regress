"""Progress remains visible when Regress is run through a pipe or log collector."""

from __future__ import annotations

import io
import time

from rich.console import Console

from regress import ui


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
