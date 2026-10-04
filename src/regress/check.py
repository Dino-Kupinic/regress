"""Score the existing tests with mutation testing, without any model: a quality gate for CI."""

from __future__ import annotations

import dataclasses
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from regress.batch import Target
from regress.changes import code_ranges, format_ranges
from regress.errors import RegressError
from regress.models import Mutant, MutantStatus, MutationRun
from regress.mutants import mutated_line
from regress.project import Runner, load_project
from regress.store import RunStore
from regress.stryker import run_stryker
from regress.ui import Reporter
from regress.validate import Workspace
from regress.vitest import run_vitest

CheckStatus = Literal["pending", "scored", "no-tests", "failed", "skipped"]
COMMENT_MARKER = "<!-- regress-check -->"


class Survivor(BaseModel):
    """An undetected mutant, described the way the improve prompt and the web app show it."""

    id: str
    mutator: str
    status: str
    line: int
    original: str
    mutated: str

    @classmethod
    def of(cls, mutant: Mutant, source_lines: list[str]) -> Survivor:
        pair = mutated_line(mutant, source_lines)
        original, mutated = pair or (_one_line(mutant.original), _one_line(mutant.replacement) or "(removed)")
        return cls(
            id=mutant.id,
            mutator=mutant.mutator,
            status=mutant.status.value,
            line=mutant.start_line,
            original=original,
            mutated=mutated,
        )


class CheckFile(BaseModel):
    source_file: str
    test_file: str | None = None
    mutate_lines: list[tuple[int, int]] = Field(default_factory=list)
    status: CheckStatus = "pending"
    error: str | None = None
    tests: int = 0
    killed: int = 0
    timeout: int = 0
    survived: int = 0
    no_coverage: int = 0
    score: float | None = None
    survivors: list[Survivor] = Field(default_factory=list)

    @property
    def detected(self) -> int:
        return self.killed + self.timeout

    @property
    def valid(self) -> int:
        return self.detected + self.survived + self.no_coverage


class CheckReport(BaseModel):
    id: str
    created_at: datetime
    project_root: str
    changed_since: str | None = None
    fail_under: float | None = None
    files: list[CheckFile] = Field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def score(self) -> float | None:
        """Every mutant of the scored files together. Files without tests have no mutants to add; see `passed`."""
        scored = [f for f in self.files if f.status == "scored"]
        valid = sum(f.valid for f in scored)
        return 100.0 * sum(f.detected for f in scored) / valid if valid else None

    @property
    def errors(self) -> list[CheckFile]:
        return [f for f in self.files if f.status == "failed"]

    @property
    def untested(self) -> list[CheckFile]:
        return [f for f in self.files if f.status == "no-tests"]

    @property
    def passed(self) -> bool:
        """No file errored and, with a threshold, the combined score meets it and every file has tests."""
        if self.errors:
            return False
        if self.fail_under is None:
            return True
        if self.untested:
            return False
        score = self.score
        return score is None or score >= self.fail_under


def run_check(
    root: Path,
    targets: list[Target],
    runner: Runner = "auto",
    reporter: Reporter | None = None,
    changed_since: str | None = None,
    fail_under: float | None = None,
    max_survivors: int = 10,
    vitest_timeout: float = 300,
    stryker_timeout: float = 1800,
) -> tuple[CheckReport, Path]:
    """Mutation-test each target's existing test file. Saves artifacts under `.regress/checks/<id>/`."""
    started = time.monotonic()
    reporter = reporter or Reporter()
    store = RunStore(root)
    directory = store.create_check_dir()
    report = CheckReport(
        id=directory.name,
        created_at=datetime.now().astimezone(),
        project_root=str(root),
        changed_since=changed_since,
        fail_under=fail_under,
        files=[CheckFile(source_file=t.source.relative_to(root).as_posix()) for t in targets],
    )
    try:
        for index, (target, result) in enumerate(zip(targets, report.files, strict=True), start=1):
            with reporter.activity(f"Checking {result.source_file} ({index}/{len(targets)})"):
                _check_one(
                    target, result, runner, directory / f"{index:02d}", max_survivors, vitest_timeout, stryker_timeout
                )
            store.save_check(report, directory)
    finally:
        report.duration_seconds = round(time.monotonic() - started, 1)
        store.save_check(report, directory)
    return report, directory


def _check_one(
    target: Target,
    result: CheckFile,
    runner: Runner,
    output_dir: Path,
    max_survivors: int,
    vitest_timeout: float,
    stryker_timeout: float,
) -> None:
    try:
        source = target.source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        result.status, result.error = "failed", f"Unreadable source file: {error}"
        return
    lines = code_ranges(source, target.lines) if target.lines is not None else []
    if target.lines is not None and not lines:
        result.status, result.error = "skipped", "Only comments, imports or blank lines changed."
        return
    result.mutate_lines = lines
    try:
        project = dataclasses.replace(load_project(target.source, None, runner), mutate_lines=tuple(lines))
        result.test_file = project.test_rel
        if not project.test_file.is_file():
            result.status, result.error = "no-tests", f"No test file; Regress would create {project.test_rel}."
            return
        workspace = Workspace(project)
        try:
            tests = run_vitest(project, output_dir / "vitest", "existing", vitest_timeout)
            if not tests.success:
                failing = [t.full_name for t in tests.failed][:5] or (tests.suite_errors + tests.run_errors)[:1]
                result.status = "failed"
                result.error = f"The existing tests in {project.test_rel} do not pass: " + "; ".join(failing)
                return
            result.tests = tests.total
            run = run_stryker(project, output_dir / "stryker", 1, stryker_timeout)
        finally:
            if not workspace.source_intact():  # puts the original back
                raise RegressError("The source file changed during the check; it has been restored.")
    except RegressError as error:
        result.status, result.error = "failed", str(error)
        return
    _score(result, run, source.splitlines(keepends=True), max_survivors)


def _score(result: CheckFile, run: MutationRun, source_lines: list[str], max_survivors: int) -> None:
    result.status = "scored"
    result.killed, result.timeout = run.killed, run.timeout
    result.survived, result.no_coverage = run.survived, run.no_coverage
    result.score = run.score if result.valid else None
    result.survivors = [Survivor.of(m, source_lines) for m in spread_survivors(run, max_survivors)]


def spread_survivors(run: MutationRun, limit: int) -> list[Mutant]:
    """Undetected mutants to show a reviewer: one per source line before a second on any line, in line order.

    A mutant that survived (the tests ran the code but asserted nothing about it) comes before one no test
    reached on the same line.
    """
    undetected = sorted(run.undetected, key=lambda m: (m.start_line, m.status != MutantStatus.SURVIVED, m.start_column))
    first, rest, seen = [], [], set()
    for mutant in undetected:
        (rest if mutant.start_line in seen else first).append(mutant)
        seen.add(mutant.start_line)
    return sorted((first + rest)[:limit], key=lambda m: (m.start_line, m.start_column))


def render_markdown(report: CheckReport, max_survivors: int = 5) -> str:
    """A pull request comment: the gate's verdict, a score per file, and the top surviving mutants."""
    score = report.score
    verdict = "✅ passed" if report.passed else "❌ failed"
    gate = f" (threshold {report.fail_under:g}%)" if report.fail_under is not None else ""
    since = f" for files changed since `{report.changed_since}`" if report.changed_since else ""
    out = [COMMENT_MARKER, f"### Regress mutation check: {verdict}{gate}", ""]
    out.append(f"Combined mutation score{since}: **{_percent(score)}**.")
    out += ["", "| File | Lines | Tests | Mutants | Score | |", "| --- | --- | ---: | ---: | ---: | --- |"]
    for f in report.files:
        lines = format_ranges(f.mutate_lines) if f.mutate_lines else "—" if f.status == "skipped" else "all"
        mutants = f"{f.detected}/{f.valid}" if f.status == "scored" else "—"
        note = {"scored": "", "no-tests": "no tests", "skipped": "skipped", "failed": "error"}.get(f.status, f.status)
        tests = str(f.tests) if f.status == "scored" else "—"
        out.append(f"| `{f.source_file}` | {lines} | {tests} | {mutants} | {_percent(f.score)} | {note} |")
    problems = [f for f in report.files if f.status in ("failed", "no-tests")]
    if problems:
        out.append("")
        for f in problems:
            out.append(f"- `{f.source_file}`: {_escape(f.error or f.status)}")
    survivors = [(f, s) for f in report.files for s in f.survivors[:max_survivors]]
    if survivors:
        out += ["", "<details><summary>Surviving mutants: changes no test noticed</summary>", ""]
        for f, s in survivors:
            where = f"{f.source_file}:{s.line}"
            tag = "no coverage" if s.status == "NoCoverage" else "survived"
            out += [f"**{s.mutator}** · `{where}` ({tag})", "```diff", f"- {s.original}", f"+ {s.mutated}", "```"]
        out += ["</details>"]
    out += ["", "<sub>Run `regress run <file>` locally to have tests written for these mutants.</sub>", ""]
    return "\n".join(out)


def _percent(score: float | None) -> str:
    return "—" if score is None else f"{score:.0f}%"


def _escape(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _one_line(text: str, width: int = 100) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= width else collapsed[: width - 1] + "…"
