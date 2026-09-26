from __future__ import annotations

import json
from pathlib import Path

from regress.errors import ToolError
from regress.models import TestCase, TestRunResult, TestStatus
from regress.process import run_command, strip_ansi, tail
from regress.project import Project


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
        f"--outputFile={report_path}",
        "--coverage.enabled=false",
    )
    result = run_command(args, cwd=project.root, timeout=timeout, log_path=output_dir / f"{name}.log")
    if not report_path.is_file():
        raise ToolError(f"Vitest did not produce a report (exit code {result.returncode}).", tail(result.output))
    return parse_vitest_report(json.loads(report_path.read_text()), result.output)


def parse_vitest_report(report: dict, output: str = "") -> TestRunResult:
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
    success = bool(report.get("success")) and not suite_errors and not any(t.status == TestStatus.FAILED for t in tests)
    if not report.get("testResults") and not suite_errors:
        suite_errors.append("Vitest found no test file to run. Check the `include` patterns in your Vitest config.")
        success = False
    return TestRunResult(success=success, tests=tests, suite_errors=suite_errors, output=tail(output, 60))
