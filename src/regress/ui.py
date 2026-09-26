from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from regress.models import MutantStatus, MutationRun, RunReport, Stage
from regress.mutants import short_description

if TYPE_CHECKING:
    from regress.project import Project

LABEL_WIDTH = 18
VALUE_WIDTH = 6


class Reporter:
    """Pipeline progress callbacks. The base class is silent."""

    def start(self, project: Project, report: RunReport) -> None: ...
    def activity(self, message: str) -> AbstractContextManager[object]:
        return nullcontext()

    def baseline(self, stage: Stage, exists: bool) -> None: ...
    def generated(self, baseline: Stage, stage: Stage) -> None: ...
    def rejected(self, attempt: int, attempts: int, problems: list[str]) -> None: ...
    def mutation(self, stage: Stage) -> None: ...
    def improving(self, round_number: int, selected: int, undetected: int) -> None: ...
    def improved(self, previous: Stage, stage: Stage) -> None: ...
    def warn(self, message: str) -> None: ...
    def note(self, message: str) -> None: ...
    def finish(self, report: RunReport, run_dir: Path) -> None: ...


def score_style(score: float) -> str:
    return "green" if score >= 80 else "yellow" if score >= 60 else "red"


def format_score(score: float | None) -> str:
    return "—" if score is None else f"{score:.0f}%"


def format_delta(delta: float) -> str:
    return f"{delta:+.0f}%"


class ConsoleReporter(Reporter):
    def __init__(self, console: Console | None = None, verbose: bool = False) -> None:
        self.console = console or Console(highlight=False)
        self.verbose = verbose
        self._runs = 0

    def _row(self, label: str, value: object, style: str = "bold") -> None:
        self.console.print(f"{label:<{LABEL_WIDTH}}[{style}]{str(value):>{VALUE_WIDTH}}[/]")

    def start(self, project: Project, report: RunReport) -> None:
        self.console.print("[bold]regress[/]\n")
        self.console.print(f"Analyzing [bold]{escape(project.source_rel)}[/]...")
        tests = project.test_rel + ("" if report.test_file_existed else " [dim](will be created)[/]")
        root = _display_path(project.root)
        self.console.print(f"[dim]  project  {escape(root)} ({project.toolchain.describe()})[/]")
        self.console.print(f"[dim]  tests    [/][dim]{tests}[/]")
        self.console.print(f"[dim]  model    {escape(report.model)}[/]\n")

    @contextmanager
    def activity(self, message: str) -> Iterator[None]:
        with self.console.status(f"[dim]{escape(message)}...[/]", spinner="dots"):
            yield

    def baseline(self, stage: Stage, exists: bool) -> None:
        if not exists:
            self.console.print("[dim]No existing test file; starting from scratch.[/]")
        self._row("Baseline tests", stage.test_count)
        if stage.mutation is None and exists:
            self.console.print()

    def generated(self, baseline: Stage, stage: Stage) -> None:
        self._row("Generated tests", stage.test_count - baseline.test_count)
        self._row("Total tests", stage.test_count)
        self._explain(stage)
        self.console.print()

    def rejected(self, attempt: int, attempts: int, problems: list[str]) -> None:
        heading = f"[yellow]✗ Attempt {attempt}/{attempts} rejected[/]"
        if attempt < attempts:
            heading += "[dim]; asking the model to fix it[/]"
        self.console.print(heading)
        shown = problems if self.verbose else problems[:3]
        for problem in shown:
            first, *rest = problem.splitlines()
            details = [line.strip() for line in rest if line.strip()]
            if self.verbose:
                self.console.print(f"[dim]  · {escape(first)}[/]")
                for line in rest:
                    self.console.print(f"[dim]    {escape(line)}[/]")
            elif details:
                # The first detail line is usually the assertion message: the part worth seeing.
                self.console.print(f"[dim]  · {escape(first.rstrip(':'))}: {escape(details[0])}[/]")
            else:
                self.console.print(f"[dim]  · {escape(first)}[/]")
        if len(problems) > len(shown):
            self.console.print(f"[dim]  · ...and {len(problems) - len(shown)} more (use --verbose)[/]")

    def mutation(self, stage: Stage) -> None:
        run = stage.mutation
        assert run is not None
        self._runs += 1
        self.console.print(
            f"[bold]Mutation run #{self._runs}[/] [dim]({stage.label.lower()}, {run.duration_seconds:.0f}s)[/]"
        )
        self._row("Killed", run.killed + run.timeout)
        self._row("Survived", run.survived)
        if run.no_coverage:
            self._row("No coverage", run.no_coverage)
        self._row("Score", format_score(run.score), f"bold {score_style(run.score)}")
        self.console.print()

    def improving(self, round_number: int, selected: int, undetected: int) -> None:
        extra = f" [dim](sending {selected} of {undetected})[/]" if selected < undetected else ""
        self.console.print(f"Improving tests from surviving mutations...{extra}")

    def improved(self, previous: Stage, stage: Stage) -> None:
        self._row("Added tests", stage.test_count - previous.test_count)
        self._row("Total tests", stage.test_count)
        if stage.equivalent_mutants:
            self.console.print(f"[dim]Model flagged {len(stage.equivalent_mutants)} mutant(s) as likely equivalent.[/]")
        self._explain(stage)
        self.console.print()

    def _explain(self, stage: Stage) -> None:
        if self.verbose and stage.summary:
            self.console.print(f"[dim]{escape(stage.summary)}[/]")

    def warn(self, message: str) -> None:
        self.console.print(f"[yellow]! {escape(message)}[/]")

    def note(self, message: str) -> None:
        self.console.print(f"[dim]{escape(message)}[/]")

    def finish(self, report: RunReport, run_dir: Path) -> None:
        delta = report.improvement
        if delta is not None and report.kept is not report.reference:
            self._row("Improvement", format_delta(delta), "bold green" if delta > 0 else "bold")
        baseline = report.stage("baseline")
        kept = report.kept
        if baseline and baseline.mutation and kept and kept.score is not None and kept is not baseline:
            self._row("vs existing tests", format_delta(kept.score - baseline.mutation.score), "bold")
        self.console.print()
        if kept is not None:
            self.console.print(f"[green]✓[/] Kept {kept.label.lower()} in [bold]{escape(report.test_file)}[/]")
        self.console.print(f"[dim]Report saved to {escape(_display_path(run_dir))} · view it with `regress report`[/]")


def render_report(report: RunReport, run_dir: Path, console: Console, show_mutants: int = 10) -> None:
    status = {"completed": "[green]completed[/]", "failed": "[red]failed[/]"}.get(report.status, report.status)
    console.print(f"[bold]regress report[/] [dim]{report.id}[/]\n")
    console.print(f"[dim]Source   [/]{escape(report.source_file)}")
    console.print(f"[dim]Tests    [/]{escape(report.test_file)}")
    console.print(f"[dim]Model    [/]{escape(report.model)}")
    console.print(f"[dim]Status   [/]{status} [dim]in {report.duration_seconds:.0f}s[/]")
    if report.error:
        console.print(f"[red]{escape(report.error)}[/]")
    console.print()

    table = Table(box=None, pad_edge=False, header_style="dim")
    for column in ("Stage", "Tests", "Killed", "Survived", "No cov.", "Score"):
        table.add_column(column, justify="left" if column == "Stage" else "right")
    for stage in report.stages:
        run = stage.mutation
        label = (
            stage.label
            + (" [yellow](rejected)[/]" if stage.rejected else "")
            + (" [green]✓ kept[/]" if stage.label == report.kept_stage else "")
        )
        if run is None:
            table.add_row(label, str(stage.test_count) if not stage.rejected else "—", "—", "—", "—", "—")
            continue
        table.add_row(
            label,
            str(stage.test_count),
            str(run.killed + run.timeout),
            str(run.survived),
            str(run.no_coverage),
            f"[{score_style(run.score)}]{format_score(run.score)}[/]",
        )
    console.print(table)

    delta = report.improvement
    if delta is not None and report.kept is not report.reference and report.reference is not None:
        console.print(f"\n[bold]Improvement[/] {format_delta(delta)} [dim](vs {report.reference.label.lower()})[/]")
    usage = report.usage
    if usage.calls:
        console.print(
            f"[dim]LLM: {usage.calls} calls · {usage.input_tokens:,} input / {usage.output_tokens:,} output tokens[/]"
        )

    kept = report.kept
    if kept and kept.mutation and show_mutants:
        _render_survivors(kept.mutation, report, run_dir, console, show_mutants, set(kept.equivalent_mutants))
    console.print(f"\n[dim]Artifacts: {escape(_display_path(run_dir))}[/]")


def _render_survivors(
    run: MutationRun, report: RunReport, run_dir: Path, console: Console, limit: int, equivalent: set[str]
) -> None:
    undetected = run.undetected
    if not undetected:
        console.print("\n[green]Every mutant was detected.[/]")
        return
    source_path = Path(report.project_root) / report.source_file
    lines = source_path.read_text().splitlines(keepends=True) if source_path.is_file() else []
    console.print(f"\n[bold]Undetected mutants[/] [dim]({len(undetected)} in the kept tests)[/]")
    for mutant in undetected[:limit]:
        tag = "no coverage" if mutant.status == MutantStatus.NO_COVERAGE else "survived"
        if mutant.id in equivalent:
            tag += ", likely equivalent"
        where = f"{report.source_file}:{mutant.start_line}"
        detail = short_description(mutant, lines) if lines else f"{mutant.original} → {mutant.replacement}"
        console.print(f"  [dim]{escape(where):<22}[/] {escape(mutant.mutator):<22} {escape(detail)} [dim]({tag})[/]")
    if len(undetected) > limit:
        console.print(f"  [dim]...and {len(undetected) - limit} more (use --mutants N)[/]")


def render_run_list(reports: list[tuple[RunReport, Path]], console: Console) -> None:
    table = Table(box=None, pad_edge=False, header_style="dim")
    for column in ("Run", "Source", "Status", "First", "Kept", "Δ"):
        table.add_column(column, justify="right" if column in ("First", "Kept", "Δ") else "left")
    for report, _ in reports:
        reference, kept = report.reference, report.kept
        delta = report.improvement
        table.add_row(
            report.id,
            report.source_file,
            report.status,
            format_score(reference.score if reference else None),
            format_score(kept.score if kept else None),
            format_delta(delta) if delta is not None else "—",
        )
    console.print(table)


def _display_path(path: Path) -> str:
    try:
        return os.path.relpath(path) if path.is_relative_to(Path.cwd()) else str(path)
    except ValueError:
        return str(path)
