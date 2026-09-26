from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from regress.models import TestRunResult
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
        self.original_tests = project.test_file.read_text() if project.test_file.is_file() else None
        self._source = project.source.read_bytes()

    def write_tests(self, content: str | None) -> None:
        if content is None:
            self.project.test_file.unlink(missing_ok=True)
            return
        self.project.test_file.parent.mkdir(parents=True, exist_ok=True)
        self.project.test_file.write_text(content)

    def restore(self) -> None:
        self.write_tests(self.original_tests)

    def source_intact(self) -> bool:
        """True if the source is unchanged; otherwise puts the original back and returns False."""
        current = self.project.source.read_bytes() if self.project.source.is_file() else None
        if current == self._source:
            return True
        self.project.source.write_bytes(self._source)
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
) -> Check:
    problems = static_problems(project, content, previous)
    if problems:
        return Check(ok=False, problems=problems)

    workspace.write_tests(content)
    result = run_vitest(project, output_dir, name, timeout)
    if not workspace.source_intact():
        problems.append("Running the tests modified the source file. Tests must never write to project files.")
    for error in result.suite_errors:
        problems.append("The test file failed to load:\n" + _clip(error, 25))
    for test in result.failed[:MAX_REPORTED_FAILURES]:
        message = test.failure_messages[0] if test.failure_messages else "(no message)"
        problems.append(f'Test "{test.full_name}" fails against the current implementation:\n{_clip(message, 12)}')
    if len(result.failed) > MAX_REPORTED_FAILURES:
        problems.append(f"...and {len(result.failed) - MAX_REPORTED_FAILURES} more failing tests.")
    missing = sorted(required_tests - result.names)
    if missing:
        listing = ", ".join(f'"{name}"' for name in missing[:15])
        problems.append(f"These existing tests were removed or renamed; keep each one exactly: {listing}")
    if not problems and result.total < min_tests:
        problems.append(
            f"No new tests were added: the file has {result.total} tests, the previous version had {min_tests - 1}."
        )
    return Check(ok=not problems, problems=problems, result=result)


def _clip(text: str, max_lines: int) -> str:
    lines = text.strip().splitlines()
    if len(lines) > max_lines:
        lines = [*lines[:max_lines], "..."]
    return "\n".join(f"    {line}" for line in lines)
