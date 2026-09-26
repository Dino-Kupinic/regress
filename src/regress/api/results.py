"""Read what a run left in its directory: report, event log, test versions, mutants, model calls."""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import ValidationError

from regress.api.schemas import (
    Artifact,
    LLMCall,
    LLMCallDetail,
    MutantView,
    RunEvent,
    RunSummary,
    StageDiff,
    StageMutants,
    StageTestFile,
)
from regress.models import MutantStatus, RunReport, Stage
from regress.mutants import mutated_line, short_description
from regress.project import SOURCE_EXTENSIONS

EVENTS_FILE = "events.jsonl"
MutantFilter = Literal["undetected", "detected", "all"]

_LLM_CALL = re.compile(r"(\d+)-(generated|improved)-attempt(\d+)")
_DETECTED = (MutantStatus.KILLED, MutantStatus.TIMEOUT)


def summarize(report: RunReport, active: bool = False) -> RunSummary:
    baseline, reference, kept = report.stage("baseline"), report.reference, report.kept
    return RunSummary(
        id=report.id,
        created_at=report.created_at,
        source_file=report.source_file,
        test_file=report.test_file,
        model=report.model,
        status=report.status,
        error=report.error,
        kept_stage=report.kept_stage,
        duration_seconds=report.duration_seconds,
        baseline_score=baseline.score if baseline else None,
        reference_score=reference.score if reference else None,
        kept_score=kept.score if kept else None,
        improvement=report.improvement,
        tests_before=baseline.test_count if baseline else 0,
        tests_after=kept.test_count if kept else None,
        active=active,
    )


def load_events(run_dir: Path) -> list[RunEvent]:
    """The saved event log. Runs started from the CLI have none."""
    path = contained(run_dir, EVENTS_FILE)
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(RunEvent.model_validate_json(line))
        except ValidationError:
            continue  # a line cut short when the server stopped
    return events


# --- stages ----------------------------------------------------------------------------------


def stage_at(report: RunReport, index: int) -> Stage:
    if not 0 <= index < len(report.stages):
        raise HTTPException(404, f"Run {report.id} has no stage {index}; it has {len(report.stages)}.")
    return report.stages[index]


def previous_stage(report: RunReport, index: int) -> int | None:
    """The stage whose tests stage `index` started from.

    Mirrors how the pipeline picks its current tests: each accepted stage replaces them, except an
    improvement that lowered the mutation score.
    """
    current: int | None = None
    for position, stage in enumerate(report.stages):
        parent = current
        replaces = stage.kind != "improved" or current is None or _score(stage) >= _score(report.stages[current])
        if not stage.rejected and replaces:
            current = position
        if position == index:
            return parent
    return None


def stage_tests(report: RunReport, run_dir: Path, index: int) -> StageTestFile:
    stage = stage_at(report, index)
    return StageTestFile(
        stage=index,
        label=stage.label,
        kind=stage.kind,
        test_file=report.test_file,
        test_count=stage.test_count,
        content=_snapshot(run_dir, stage, required=True),
    )


def stage_diff(report: RunReport, run_dir: Path, index: int, before: int | None = None) -> StageDiff:
    """How stage `index` changed the test file, compared with `before` (by default the tests it started from)."""
    stage = stage_at(report, index)
    after = _snapshot(run_dir, stage, required=True)
    if before is None:
        before = previous_stage(report, index)
    base = report.stages[before] if before is not None else None
    old = _snapshot(run_dir, base) if base else ""
    unified = difflib.unified_diff(
        old.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{report.test_file}",
        tofile=f"b/{report.test_file}",
    )
    return StageDiff(
        stage=index,
        label=stage.label,
        before_stage=before,
        before_label=base.label if base else None,
        test_file=report.test_file,
        before=old,
        after=after,
        unified="".join(unified),
        added_tests=sorted(set(stage.test_names) - set(base.test_names if base else [])),
    )


def run_diff(report: RunReport, run_dir: Path) -> StageDiff:
    """Everything the run changed: the original test file against the one it kept."""
    kept = report.kept
    if kept is None:
        raise HTTPException(404, f"Run {report.id} kept no tests ({report.status}).")
    return stage_diff(report, run_dir, report.stages.index(kept), before=0)


def stage_mutants(report: RunReport, run_dir: Path, index: int, which: MutantFilter) -> StageMutants:
    stage = stage_at(report, index)
    if stage.mutation is None:
        raise HTTPException(404, f"Stage {index} ({stage.label}) was not mutation-tested.")
    lines = _mutated_source(report, run_dir, stage.mutation.index)
    equivalent = {mutant for s in report.stages for mutant in s.equivalent_mutants}
    views = []
    for mutant in stage.mutation.mutants:
        detected = mutant.status in _DETECTED
        if (which == "undetected" and not mutant.undetected) or (which == "detected" and not detected):
            continue
        known = mutant.end_line <= len(lines)  # the source may have changed since, if it had to be re-read
        pair = mutated_line(mutant, lines) if known else None
        views.append(
            MutantView(
                **mutant.model_dump(),
                detected=detected,
                equivalent=mutant.id in equivalent,
                original_line=pair[0] if pair else None,
                mutated_line=pair[1] if pair else None,
                summary=short_description(mutant, lines) if known else f"{mutant.original} → {mutant.replacement}",
            )
        )
    return StageMutants(
        stage=index,
        label=stage.label,
        source_file=report.source_file,
        total=len(stage.mutation.mutants),
        mutants=views,
    )


def _score(stage: Stage) -> float:
    return stage.score if stage.score is not None else -1.0


def _snapshot(run_dir: Path, stage: Stage, required: bool = False) -> str:
    if stage.test_file_snapshot is None:
        if required:
            reason = "it was rejected" if stage.rejected else "there was no test file"
            raise HTTPException(404, f"{stage.label} has no saved test file: {reason}.")
        return ""
    path = contained(run_dir, stage.test_file_snapshot)
    if not path.is_file():
        raise HTTPException(404, f"The saved test file of {stage.label} is missing.")
    return path.read_text()


def _mutated_source(report: RunReport, run_dir: Path, index: int) -> list[str]:
    """The source as Stryker saw it, from its saved report; else the file as it is now."""
    try:
        files = json.loads(contained(run_dir, f"stryker/mutation-{index}.json").read_text())["files"]
        entry = files.get(report.source_file) or next(
            value for key, value in files.items() if Path(key).as_posix().endswith(report.source_file)
        )
        return entry["source"].splitlines(keepends=True)
    except (OSError, ValueError, KeyError, StopIteration, AttributeError, TypeError):
        pass
    # Saved reports can move with their project. Never trust their original absolute path.
    source = contained(run_dir.parents[2], report.source_file)
    if source.suffix not in SOURCE_EXTENSIONS or any(p.startswith(".") for p in Path(report.source_file).parts):
        return []
    return source.read_text().splitlines(keepends=True) if source.is_file() else []


# --- model calls -----------------------------------------------------------------------------


def llm_calls(run_dir: Path) -> list[LLMCall]:
    directory = contained(run_dir, "llm")
    names = sorted(p.name.removesuffix(".response.json") for p in directory.glob("*.response.json"))
    calls = []
    for name in names:
        if _LLM_CALL.fullmatch(name):
            try:
                calls.append(_llm_call(directory, name))
            except HTTPException:
                continue  # a damaged artifact must not hide every other model call
    return calls


def llm_call(run_dir: Path, name: str) -> LLMCallDetail:
    directory = contained(run_dir, "llm")
    if not _LLM_CALL.fullmatch(name) or not (directory / f"{name}.response.json").is_file():
        raise HTTPException(404, f"No model call {name}.")
    call = _llm_call(directory, name)
    prompt = contained(directory, f"{name}.prompt.md")
    response = _response(directory, name)
    return LLMCallDetail(
        **call.model_dump(),
        prompt=prompt.read_text() if prompt.is_file() else "",
        test_file=response.get("test_file", ""),
    )


def _llm_call(directory: Path, name: str) -> LLMCall:
    match = _LLM_CALL.fullmatch(name)
    assert match is not None
    response = _response(directory, name)
    usage = response.get("usage", {})
    try:
        return LLMCall(
            name=name,
            kind=match[2],
            attempt=int(match[3]),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            summary=response.get("summary", ""),
            new_tests=response.get("new_tests", []),
            equivalent_mutants=response.get("equivalent_mutants", []),
        )
    except (ValidationError, AttributeError, ValueError) as error:
        raise HTTPException(404, f"Model call {name} is incomplete or invalid.") from error


def _response(directory: Path, name: str) -> dict:
    try:
        data = json.loads(contained(directory, f"{name}.response.json").read_text())
        if not isinstance(data, dict) or not isinstance(data.get("test_file", ""), str):
            raise ValueError("Invalid response")
        return data
    except (OSError, ValueError) as error:
        raise HTTPException(404, f"Model call {name} is incomplete or invalid.") from error


# --- raw files -------------------------------------------------------------------------------


def artifacts(run_dir: Path) -> list[Artifact]:
    found = []
    for directory, dirs, files in run_dir.walk():
        if directory == run_dir / "stryker" and "tmp" in dirs:
            dirs.remove("tmp")  # do not traverse Stryker's potentially large sandbox
        for name in files:
            path = directory / name
            if path.is_symlink() or not path.is_file():
                continue
            try:
                found.append(Artifact(path=path.relative_to(run_dir).as_posix(), size=path.stat().st_size))
            except FileNotFoundError:
                continue  # an active run may remove temporary files while we list
    return sorted(found, key=lambda artifact: artifact.path)


def contained(base: Path, relative: str) -> Path:
    """`base / relative`, refusing paths that lead outside `base`."""
    try:
        if Path(relative).is_absolute():
            raise ValueError("Expected a relative path")
        path = (base / relative).resolve()
    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(404, f"No file {relative}.") from error
    if not path.is_relative_to(base.resolve()):
        raise HTTPException(404, f"No file {relative}.")
    return path
