from __future__ import annotations

import json
import os
from pathlib import Path

from regress.errors import ToolError
from regress.files import atomic_write_text
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
    atomic_write_text(config_path, json.dumps(stryker_config(project, report_path, temp_dir), indent=2))

    args = project.toolchain.command("stryker", "run", _rel(config_path, project.root))
    result = run_command(args, cwd=project.root, timeout=timeout, log_path=output_dir / f"stryker-{index}.log")
    if result.returncode != 0 or not report_path.is_file():
        raise ToolError(f"Stryker failed (exit code {result.returncode}).", _stryker_error(result.output))
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ToolError(f"Stryker produced an unreadable report: {error}", tail(result.output)) from error
    run = parse_stryker_report(payload, project.source_rel, index)
    run.duration_seconds = round(result.duration_seconds, 1)
    return run


def parse_stryker_report(report: dict, source_rel: str, index: int) -> MutationRun:
    try:
        files = report["files"]
        entry = files.get(source_rel)
        if entry is None:
            matches = [value for key, value in files.items() if key.replace("\\", "/").endswith("/" + source_rel)]
            if len(matches) > 1:
                raise ToolError(f"Stryker report has ambiguous results for {source_rel}.")
            entry = matches[0] if matches else None
        if entry is None:
            raise ToolError(f"Stryker report has no results for {source_rel}.")
        source = entry["source"]
        lines = source.splitlines(keepends=True)
        if not lines or source.endswith("\n"):
            lines.append("")
        mutants = [_parse_mutant(raw, lines) for raw in entry["mutants"]]
        if len({m.id for m in mutants}) != len(mutants):
            raise ValueError("duplicate mutant IDs")
    except (AttributeError, KeyError, IndexError, TypeError, ValueError) as error:
        raise ToolError(f"Stryker produced an invalid report for {source_rel}: {error}") from error
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
    if (
        not all(type(value) is int and value >= 1 for value in (start_line, start_col, end_line, end_col))
        or start_line > end_line
        or end_line > len(lines)
        or (start_line == end_line and start_col > end_col)
        or start_col > len(lines[start_line - 1].encode("utf-16-le")) // 2 + 1
        or end_col > len(lines[end_line - 1].encode("utf-16-le")) // 2 + 1
    ):
        raise ValueError("invalid source location")
    if start_line == end_line:
        return _column_span(lines[start_line - 1], start_col, end_col)
    parts = [
        _column_span(lines[start_line - 1], start_col),
        *lines[start_line : end_line - 1],
        _column_span(lines[end_line - 1], 1, end_col),
    ]
    return "".join(parts)


def _column_span(line: str, start: int, end: int | None = None) -> str:
    # JavaScript tool locations count UTF-16 code units rather than Python code points.
    raw = line.encode("utf-16-le")
    return raw[(start - 1) * 2 : (end - 1) * 2 if end is not None else None].decode("utf-16-le")


def _stryker_error(output: str) -> str:
    """The useful part of a Stryker failure: its error lines, else the log tail."""
    lines = output.splitlines()
    for i, line in enumerate(lines):
        if "ERROR" in line or "Error:" in line:
            return "\n".join(lines[i : i + 30])
    return tail(output)


def _rel(path: Path, root: Path) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")
