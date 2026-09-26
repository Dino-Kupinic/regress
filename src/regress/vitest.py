from __future__ import annotations

import json
import re
from pathlib import Path

from regress.errors import ToolError
from regress.models import TestCase, TestRunResult, TestStatus
from regress.process import run_command, strip_ansi, tail
from regress.project import Project

# Where Vitest's console reporters start describing errors that belong to no single test.
_ERROR_SECTION = re.compile(r"⎯{2,}\s*(?:Failed Suites|Unhandled)")
MAX_RUN_ERROR_LINES = 40


def run_vitest(project: Project, output_dir: Path, name: str, timeout: float = 300) -> TestRunResult:
    """Run only the project's target test file and parse Vitest's JSON report."""
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{name}.json"
    report_path.unlink(missing_ok=True)
    args = project.toolchain.command(
        "vitest",
        "run",
        str(project.test_file),
        "--reporter=json",
        # The JSON report leaves out failing hooks and unhandled errors; a console reporter prints them.
        "--reporter=dot",
        f"--outputFile={report_path}",
        "--coverage.enabled=false",
    )
    result = run_command(args, cwd=project.root, timeout=timeout, log_path=output_dir / f"{name}.log")
    if not report_path.is_file():
        raise ToolError(f"Vitest did not produce a report (exit code {result.returncode}).", tail(result.output))
    return parse_vitest_report(json.loads(report_path.read_text()), result.output, result.returncode)


def parse_vitest_report(report: dict, output: str = "", returncode: int = 0) -> TestRunResult:
    tests: list[TestCase] = []
    suite_errors: list[str] = []
    for suite in report.get("testResults", []):
        message = strip_ansi(suite.get("message") or "").strip()
        if message:
            suite_errors.append(message)
        for assertion in suite.get("assertionResults", []):
            status = assertion.get("status", "failed")
            tests.append(
                TestCase(
                    full_name=assertion.get("fullName") or assertion.get("title", ""),
                    status=TestStatus(status) if status in TestStatus._value2member_map_ else TestStatus.FAILED,
                    failure_messages=[strip_ansi(m) for m in assertion.get("failureMessages", [])],
                )
            )
    failed = any(t.status == TestStatus.FAILED for t in tests)
    success = bool(report.get("success")) and returncode == 0 and not suite_errors and not failed
    if not report.get("testResults") and not suite_errors:
        suite_errors.append("Vitest found no test file to run. Check the `include` patterns in your Vitest config.")
        success = False
    run_errors = [] if success or suite_errors or failed else [_run_error(output, returncode)]
    return TestRunResult(
        success=success, tests=tests, suite_errors=suite_errors, run_errors=run_errors, output=tail(output, 60)
    )


def _run_error(output: str, returncode: int) -> str:
    """Vitest's own account of a failed run in which no test failed, without stack frames in dependencies."""
    lines = output.splitlines()
    start = next((i for i, line in enumerate(lines) if _ERROR_SECTION.search(line)), None)
    end = next((i for i in range(start or 0, len(lines)) if lines[i].strip().startswith("Test Files")), len(lines))
    excerpt = [
        line
        for line in lines[start or 0 : end]
        if line.strip() and "node_modules" not in line and not line.startswith("JSON report written")
    ]
    if start is None:  # no error section we recognize: keep the end of the output
        excerpt = [f"Vitest exited with code {returncode} although no test failed.", *excerpt[-20:]]
    if len(excerpt) > MAX_RUN_ERROR_LINES:
        excerpt = [*excerpt[:MAX_RUN_ERROR_LINES], "..."]
    return "\n".join(excerpt)
