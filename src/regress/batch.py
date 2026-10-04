"""Runs over several source files, one after another, with a combined summary."""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from regress.changes import LineRange, changed_lines, code_ranges
from regress.errors import ProjectError, RegressError
from regress.llm import LLM
from regress.models import MutationRun, RunReport, RunStatus
from regress.pipeline import Pipeline, RunOptions
from regress.project import Runner, find_project_root, load_project, source_files
from regress.store import RunStore
from regress.ui import Reporter

FileStatus = Literal["pending", "running", "completed", "failed", "cancelled", "skipped"]


@dataclass(frozen=True)
class Target:
    source: Path
    # Changed lines to mutate. None mutates the whole file; an empty list means nothing changed worth testing.
    lines: list[LineRange] | None = None


def resolve_targets(paths: list[Path], changed_since: str | None = None) -> tuple[Path, list[Target]]:
    """The project root and the source files that files and directories stand for.

    With `changed_since`, only source files git sees as changed since that ref count, each limited to its
    changed lines. All paths must belong to the same project.
    """
    resolved = [path.expanduser().resolve() for path in paths]
    for path in resolved:
        if not path.exists():
            raise ProjectError(f"No such file or directory: {path}")
    roots = {find_project_root(path if path.is_dir() else path.parent) for path in resolved}
    if len(roots) > 1:
        listing = "\n".join(f"  {root}" for root in sorted(roots))
        raise ProjectError(f"The paths belong to different projects; run each one separately:\n{listing}")
    root = roots.pop()
    for path in resolved:
        if path.is_dir() and not path.is_relative_to(root):
            raise ProjectError(f"{path} is outside the project at {root}.")

    if changed_since is None:
        found: list[Path] = []
        for path in resolved:
            for source in source_files(root, under=path) if path.is_dir() else [path]:
                if source not in found:
                    found.append(source)
        return root, [Target(source) for source in found]

    changes = changed_lines(root, changed_since)
    targets = [
        Target(source, changes[source])
        for source in source_files(root)
        if source in changes and any(source == path or source.is_relative_to(path) for path in resolved)
    ]
    return root, targets


class FileResult(BaseModel):
    source_file: str
    mutate_lines: list[tuple[int, int]] = Field(default_factory=list)
    status: FileStatus = "pending"
    run_id: str | None = None
    error: str | None = None
    tests_first: int | None = None
    tests_kept: int | None = None
    score_first: float | None = None
    score_kept: float | None = None
    # Detected and valid mutants (killed + timeout, and those plus survived + no coverage), for combined scores.
    detected_first: int = 0
    detected_kept: int = 0
    valid_mutants: int = 0

    @property
    def measured(self) -> bool:
        return self.status == "completed" and self.score_first is not None and self.score_kept is not None


class BatchTotals(BaseModel):
    files: int
    score_first: float | None
    score_kept: float | None


class BatchReport(BaseModel):
    id: str
    created_at: datetime
    project_root: str
    changed_since: str | None = None
    model: str
    status: RunStatus = "running"
    files: list[FileResult] = Field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def totals(self) -> BatchTotals:
        """Combined mutation scores over the files measured both first and last, so both columns cover the same."""
        measured = [f for f in self.files if f.measured]
        valid = sum(f.valid_mutants for f in measured)
        return BatchTotals(
            files=len(measured),
            score_first=100.0 * sum(f.detected_first for f in measured) / valid if valid else None,
            score_kept=100.0 * sum(f.detected_kept for f in measured) / valid if valid else None,
        )

    @property
    def failed(self) -> list[FileResult]:
        return [f for f in self.files if f.status == "failed"]


def run_batch(
    root: Path,
    targets: list[Target],
    llm: LLM,
    options: RunOptions,
    runner: Runner = "auto",
    reporter: Reporter | None = None,
    store: RunStore | None = None,
    changed_since: str | None = None,
) -> BatchReport:
    """Run the pipeline on each target in turn. A failing file is recorded and the rest still run."""
    started = time.monotonic()
    reporter = reporter or Reporter()
    store = store or RunStore(root)
    report = BatchReport(
        id=store.create_batch_id(),
        created_at=datetime.now().astimezone(),
        project_root=str(root),
        changed_since=changed_since,
        model=llm.model,
        files=[FileResult(source_file=t.source.relative_to(root).as_posix()) for t in targets],
    )
    try:
        for index, (target, result) in enumerate(zip(targets, report.files, strict=True), start=1):
            reporter.file_start(index, len(targets), result)
            try:
                _run_one(target, result, llm, options, runner, reporter, store, report.id)
            except KeyboardInterrupt:
                result.status = "cancelled"
                raise
            finally:
                store.save_batch(report)
            reporter.file_end(result)
        report.status = "failed" if report.failed else "completed"
    except BaseException:
        report.status = "cancelled"
        raise
    finally:
        report.duration_seconds = round(time.monotonic() - started, 1)
        store.save_batch(report)
    reporter.batch_finish(report, store.batch_path(report.id))
    return report


def _run_one(
    target: Target,
    result: FileResult,
    llm: LLM,
    options: RunOptions,
    runner: Runner,
    reporter: Reporter,
    store: RunStore,
    batch_id: str,
) -> None:
    lines: list[LineRange] = []
    if target.lines is not None:
        try:
            lines = code_ranges(target.source.read_text(encoding="utf-8"), target.lines)
        except (OSError, UnicodeDecodeError) as error:
            result.status, result.error = "failed", f"Unreadable source file: {error}"
            return
        if not lines:
            result.status, result.error = "skipped", "Only comments, imports or blank lines changed."
            return
    result.mutate_lines = lines
    pipeline: Pipeline | None = None
    try:
        project = dataclasses.replace(load_project(target.source, None, runner), mutate_lines=tuple(lines))
        pipeline = Pipeline(project, llm, options, reporter, store, batch_id=batch_id)
        summarize_run(pipeline.run(), result)
    except RegressError as error:
        result.status, result.error = "failed", str(error)
    except Exception as error:  # keep going with the other files; the run's own report has the details
        result.status, result.error = "failed", f"Unexpected {type(error).__name__}: {error}"
    finally:
        run_dir = getattr(pipeline, "run_dir", None)
        if run_dir is not None:
            result.run_id = run_dir.name


def summarize_run(run: RunReport, result: FileResult) -> None:
    """Fill in a completed file's result from its run report."""
    result.status = "completed"
    first, kept = run.reference, run.kept
    if first is not None:
        result.tests_first, result.score_first = first.test_count, first.score
    if kept is not None:
        result.tests_kept, result.score_kept = kept.test_count, kept.score
    if first is not None and first.mutation and kept is not None and kept.mutation:
        result.detected_first = _detected(first.mutation)
        result.detected_kept = _detected(kept.mutation)
        result.valid_mutants = _valid(kept.mutation)
        if not result.valid_mutants:  # nothing to mutate on these lines: no score to compare
            result.score_first = result.score_kept = None


def _detected(run: MutationRun) -> int:
    return run.killed + run.timeout


def _valid(run: MutationRun) -> int:
    return run.killed + run.timeout + run.survived + run.no_coverage
