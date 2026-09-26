from __future__ import annotations

import json
import os
from pathlib import Path

from regress.errors import ToolError
from regress.models import Mutant, MutantStatus, MutationRun
from regress.process import run_command, tail
from regress.project import Project


def stryker_config(project: Project, report_path: Path, temp_dir: Path) -> dict:
    return {
        "testRunner": "vitest",
        "mutate": [project.source_rel],
        # Only the target test file counts, so scores reflect what that file alone can detect.
        "testFiles": [project.test_rel],
        "coverageAnalysis": "perTest",
        "reporters": ["json"],
        "jsonReporter": {"fileName": _rel(report_path, project.root)},
        "tempDirName": _rel(temp_dir, project.root),
        "cleanTempDir": "always",
        "ignorePatterns": [".regress"],
        "incremental": False,
        "thresholds": {"high": 80, "low": 60, "break": None},
        "logLevel": "info",
        "fileLogLevel": "off",
    }


def run_stryker(project: Project, output_dir: Path, index: int, timeout: float = 1800) -> MutationRun:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"mutation-{index}.json"
    config_path = output_dir / f"stryker-{index}.config.json"
    report_path.unlink(missing_ok=True)
    temp_dir = output_dir / "tmp"  # per run, so concurrent runs never share a sandbox
    config_path.write_text(json.dumps(stryker_config(project, report_path, temp_dir), indent=2))

    args = project.toolchain.command("stryker", "run", _rel(config_path, project.root))
    result = run_command(args, cwd=project.root, timeout=timeout, log_path=output_dir / f"stryker-{index}.log")
    if result.returncode != 0 or not report_path.is_file():
        raise ToolError(f"Stryker failed (exit code {result.returncode}).", _stryker_error(result.output))
    run = parse_stryker_report(json.loads(report_path.read_text()), project.source_rel, index)
    run.duration_seconds = round(result.duration_seconds, 1)
    return run


def parse_stryker_report(report: dict, source_rel: str, index: int) -> MutationRun:
    files = report.get("files", {})
    entry = files.get(source_rel) or next(
        (value for key, value in files.items() if Path(key).as_posix().endswith(source_rel)), None
    )
    if entry is None:
        raise ToolError(f"Stryker report has no results for {source_rel}.")
    lines = entry["source"].splitlines(keepends=True)
    mutants = [_parse_mutant(raw, lines) for raw in entry.get("mutants", [])]
    mutants.sort(key=lambda m: (m.start_line, m.start_column, int(m.id) if m.id.isdigit() else 0))
    return MutationRun(index=index, mutants=mutants)


def _parse_mutant(raw: dict, lines: list[str]) -> Mutant:
    start, end = raw["location"]["start"], raw["location"]["end"]
    status = raw.get("status", "Pending")
    return Mutant(
        id=str(raw["id"]),
        mutator=raw.get("mutatorName", "?"),
        status=MutantStatus(status) if status in MutantStatus._value2member_map_ else MutantStatus.PENDING,
        replacement=raw.get("replacement", ""),
        original=source_span(lines, start["line"], start["column"], end["line"], end["column"]),
        start_line=start["line"],
        start_column=start["column"],
        end_line=end["line"],
        end_column=end["column"],
        killed_by=[str(t) for t in raw.get("killedBy") or []],
        covered_by=[str(t) for t in raw.get("coveredBy") or []],
        status_reason=raw.get("statusReason"),
    )


def source_span(lines: list[str], start_line: int, start_col: int, end_line: int, end_col: int) -> str:
    """Text between two 1-based (line, column) positions, end exclusive."""
    if start_line == end_line:
        return lines[start_line - 1][start_col - 1 : end_col - 1]
    parts = [
        lines[start_line - 1][start_col - 1 :],
        *lines[start_line : end_line - 1],
        lines[end_line - 1][: end_col - 1],
    ]
    return "".join(parts)


def _stryker_error(output: str) -> str:
    """The useful part of a Stryker failure: its error lines, else the log tail."""
    lines = output.splitlines()
    for i, line in enumerate(lines):
        if "ERROR" in line or "Error:" in line:
            return "\n".join(lines[i : i + 30])
    return tail(output)


def _rel(path: Path, root: Path) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")
