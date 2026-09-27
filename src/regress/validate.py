from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from regress.errors import ToolTimeout
from regress.files import atomic_write_bytes, atomic_write_text
from regress.models import TestRunResult, TestStatus
from regress.project import Project, import_specifiers, mock_specifiers, refers_to
from regress.vitest import run_vitest

_ONLY_RE = re.compile(r"\b(?:it|test|describe)\.only\s*\(")
_SKIP_RE = re.compile(r"\b(?:it|test|describe)\.(?:skip|todo|fails|skipIf|runIf)\b")
_FENCE_RE = re.compile(r"^\s*```[\w-]*\s*\n(.*?)\n\s*```\s*$", re.DOTALL)
MAX_REPORTED_FAILURES = 8


class Workspace:
    """Owns every write to the target test file and guards the source file against changes."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self._original_test_bytes = project.test_file.read_bytes() if project.test_file.is_file() else None
        self.original_tests = (
            self._original_test_bytes.decode("utf-8") if self._original_test_bytes is not None else None
        )
        self._source = project.source.read_bytes()

    def write_tests(self, content: str | None) -> None:
        if content is None:
            self.project.test_file.unlink(missing_ok=True)
            return
        self.project.test_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.project.test_file, content)

    def restore(self) -> None:
        if self._original_test_bytes is None:
            self.project.test_file.unlink(missing_ok=True)
        else:
            atomic_write_bytes(self.project.test_file, self._original_test_bytes)

    def source_intact(self) -> bool:
        """True if the source is unchanged; otherwise puts the original back and returns False."""
        current = self.project.source.read_bytes() if self.project.source.is_file() else None
        if current == self._source and not self.project.source.is_symlink():
            return True
        atomic_write_bytes(self.project.source, self._source)
        return False


@dataclass
class Check:
    ok: bool
    problems: list[str] = field(default_factory=list)
    result: TestRunResult | None = None


def clean_test_file(content: str) -> str:
    """Strip a markdown fence the model may have wrapped around the file."""
    match = _FENCE_RE.match(content)
    if match:
        content = match.group(1)
    return content.strip("\n") + "\n"


def static_problems(project: Project, content: str, previous: str | None) -> list[str]:
    if not content.strip():
        return ["The test file is empty."]
    problems = []
    if not any(refers_to(project.test_file, spec, project.source) for spec in import_specifiers(content)):
        problems.append(f'The test file must import the module under test from "{project.import_path}".')
    if any(refers_to(project.test_file, spec, project.source) for spec in mock_specifiers(content)):
        problems.append("Do not mock the module under test: mutation testing needs the real implementation.")
    if _ONLY_RE.search(content):
        problems.append("Remove `.only`: it silently skips every other test.")
    if len(_SKIP_RE.findall(content)) > len(_SKIP_RE.findall(previous or "")):
        problems.append(
            "Do not add skipped, todo, or expected-failure tests (.skip, .todo, .fails); every new test must run and pass."
        )
    return problems


def check_candidate(
    project: Project,
    workspace: Workspace,
    content: str,
    *,
    previous: str | None,
    required_tests: set[str],
    min_tests: int,
    output_dir: Path,
    name: str,
    timeout: float = 300,
    previous_result: TestRunResult | None = None,
) -> Check:
    problems = static_problems(project, content, previous)
    if problems:
        return Check(ok=False, problems=problems)

    workspace.write_tests(content)
    try:
        result = run_vitest(project, output_dir, name, timeout)
    except ToolTimeout:
        # The candidate's fault, not the toolchain's: tell the model instead of ending the run.
        result = None
    finally:
        source_intact = workspace.source_intact()
    if not source_intact:
        problems.append("Running the tests modified the source file. Tests must never write to project files.")
    if result is None:
        problems.append(_hang_problem(timeout, changed_only=bool(required_tests)))
        return Check(ok=False, problems=problems)
    for error in result.suite_errors:
        problems.append("The test file failed to load:\n" + _clip(error, 25))
    for test in result.failed[:MAX_REPORTED_FAILURES]:
        message = _without_dependency_frames(test.failure_messages[0]) if test.failure_messages else "(no message)"
        problems.append(f'Test "{test.full_name}" fails against the current implementation:\n{_clip(message, 12)}')
    for error in result.run_errors:
        problems.append(
            "Vitest reported errors outside of any single test, such as a failing beforeAll/afterAll hook or an "
            "unhandled promise rejection (always await `expect(...).resolves` and `expect(...).rejects`):\n"
            + _clip(error, 40)
        )
    if not result.success and not problems:
        problems.append("Vitest reported an unsuccessful test run.")
    if len(result.failed) > MAX_REPORTED_FAILURES:
        problems.append(f"...and {len(result.failed) - MAX_REPORTED_FAILURES} more failing tests.")
    missing = sorted(required_tests - result.names)
    if missing:
        listing = ", ".join(f'"{name}"' for name in missing[:15])
        problems.append(f"These existing tests were removed or renamed; keep each one exactly: {listing}")
    passed = Counter(test.full_name for test in result.tests if test.status == TestStatus.PASSED)
    previously_passed = (
        Counter(test.full_name for test in previous_result.tests if test.status == TestStatus.PASSED)
        if previous_result is not None
        else Counter(required_tests)
    )
    previously_skipped = Counter(test.full_name for test in previous_result.skipped) if previous_result else Counter()
    newly_skipped = Counter(test.full_name for test in result.skipped) - previously_skipped
    if newly_skipped:
        listing = ", ".join(f'"{name}"' for name in sorted(newly_skipped)[:15])
        problems.append(f"These tests were newly skipped or left pending; every new test must run and pass: {listing}")
    no_longer_passing = sorted((previously_passed - passed).keys() - set(missing))
    if no_longer_passing:
        listing = ", ".join(f'"{name}"' for name in no_longer_passing[:15])
        problems.append(f"These existing passing tests no longer pass; keep every occurrence active: {listing}")
    if not problems and result.total < min_tests:
        problems.append(
            f"No new tests were added: the file has {result.total} tests, the previous version had {min_tests - 1}."
        )
    min_passed = sum(previously_passed.values()) + 1 if previous_result is not None else min_tests
    if not problems and sum(passed.values()) < min_passed:
        problems.append("No new passing tests were added; add at least one test that runs and passes.")
    return Check(ok=not problems, problems=problems, result=result)


def _hang_problem(timeout: float, changed_only: bool) -> str:
    where = " The previous version finished, so it is in a test you added or changed." if changed_only else ""
    return (
        f"The tests did not finish within {timeout:.0f}s and were stopped: a test never ends.{where} "
        "Look for loops that cannot exit, such as a loop whose exit condition depends on the code under test, "
        "or one that waits for time to pass while vi.useFakeTimers() has frozen Date.now(). "
        "Keep every test fast and bounded."
    )


def _without_dependency_frames(message: str) -> str:
    """Drop stack frames in node_modules and native code: they crowd out the frame that points at the test."""
    return "\n".join(
        line for line in message.splitlines() if "node_modules" not in line and "(<anonymous>)" not in line
    )


def _clip(text: str, max_lines: int) -> str:
    lines = text.strip().splitlines()
    if len(lines) > max_lines:
        lines = [*lines[:max_lines], "..."]
    return "\n".join(f"    {line}" for line in lines)
