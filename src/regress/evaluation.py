"""Evaluate Regress against example modules with hidden, hand-seeded bugs.

Mutation feedback drives the improvement loop, so mutation score alone would grade Regress on
its own training signal. The hidden bugs are realistic regressions written separately (and
deliberately unlike Stryker's mutation operators); catching them measures whether better
mutation scores translate into tests that find real bugs.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, computed_field, field_validator

from regress.errors import CandidateRejected, RegressError
from regress.files import atomic_write_text
from regress.llm import LLM
from regress.models import Stage
from regress.pipeline import Pipeline, RunOptions
from regress.project import Project, Runner, load_project
from regress.store import RunStore, _check_artifact_path, regress_dir
from regress.stryker import run_stryker
from regress.ui import Reporter
from regress.validate import Workspace
from regress.vitest import run_vitest

BUGS_DIR = "hidden-bugs"
AI_LABELS = ("One-shot AI", "Regress")
COUNTING_NOTE = "Totals count only modules with a result in every column, so each column adds up the same modules."


class HiddenBug(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    description: str
    find: str = Field(min_length=1)
    replace: str


class BugSuite(BaseModel):
    name: str = ""
    source: str
    test: str
    oracle: str | None = None
    bugs: list[HiddenBug]

    @field_validator("source", "test", "oracle")
    @classmethod
    def relative_path(cls, value: str | None) -> str | None:
        if value is not None and (not value or Path(value).is_absolute() or ".." in Path(value).parts):
            raise ValueError("suite paths must be relative to the project and cannot contain '..'")
        return value

    @field_validator("bugs")
    @classmethod
    def unique_bug_ids(cls, value: list[HiddenBug]) -> list[HiddenBug]:
        if len({bug.id for bug in value}) != len(value):
            raise ValueError("hidden bug IDs must be unique")
        return value


class StageResult(BaseModel):
    label: str
    tests: int = 0
    score: float | None = None
    caught: list[str] = Field(default_factory=list)
    missed: list[str] = Field(default_factory=list)

    @property
    def bugs_total(self) -> int:
        return len(self.caught) + len(self.missed)


class ModuleResult(BaseModel):
    module: str
    stages: list[StageResult] = Field(default_factory=list)
    error: str | None = None
    note: str | None = None  # how a result was counted, e.g. when the model never wrote valid tests
    run_id: str | None = None


class StageTotal(BaseModel):
    """One column summed over the modules that have a result in every column."""

    label: str
    score: float | None = None  # mean mutation score
    caught: int = 0
    bugs_total: int = 0


class EvalResult(BaseModel):
    created_at: datetime
    mode: str
    model: str | None = None
    rounds: int | None = None
    modules: list[ModuleResult] = Field(default_factory=list)
    output_dir: str | None = None

    @property
    def stage_labels(self) -> list[str]:
        labels: list[str] = []
        for module in self.modules:
            labels += [s.label for s in module.stages if s.label not in labels]
        return labels

    @computed_field
    @property
    def counted_modules(self) -> list[str]:
        """Modules with a result in every column: the only ones totals count, so columns stay comparable."""
        labels = set(self.stage_labels)
        return [m.module for m in self.modules if m.stages and labels <= {s.label for s in m.stages}]

    @computed_field
    @property
    def totals(self) -> list[StageTotal]:
        counted = set(self.counted_modules)
        totals = []
        for label in self.stage_labels:
            stages = [s for m in self.modules if m.module in counted for s in m.stages if s.label == label]
            scores = [s.score for s in stages if s.score is not None]
            totals.append(
                StageTotal(
                    label=label,
                    score=sum(scores) / len(scores) if scores else None,
                    caught=sum(len(s.caught) for s in stages),
                    bugs_total=sum(s.bugs_total for s in stages),
                )
            )
        return totals

    @property
    def totals_label(self) -> str:
        counted, total = len(self.counted_modules), len(self.modules)
        return "All modules" if counted == total else f"{counted} of {total} modules"


def load_suites(examples_dir: Path, names: list[str] | None = None) -> list[BugSuite]:
    directory = examples_dir / BUGS_DIR
    if not directory.is_dir():
        raise RegressError(f"No {BUGS_DIR}/ directory in {examples_dir}.")
    suites = []
    for path in sorted(directory.glob("*.json")):
        try:
            suite = BugSuite.model_validate_json(path.read_text(encoding="utf-8"))
            for relative in (suite.source, suite.test, suite.oracle):
                if relative and not (examples_dir / relative).resolve().is_relative_to(examples_dir.resolve()):
                    raise ValueError(f"{relative} leads outside the project")
        except (OSError, ValueError) as error:
            raise RegressError(f"Invalid hidden bug suite {path.name}: {error}") from error
        suite.name = path.stem
        suites.append(suite)
    if names:
        unknown = set(names) - {s.name for s in suites}
        if unknown:
            raise RegressError(
                f"Unknown module(s): {', '.join(sorted(unknown))}. Available: {', '.join(s.name for s in suites)}"
            )
        suites = [s for s in suites if s.name in names]
    return suites


@contextmanager
def sandbox(root: Path) -> Iterator[Path]:
    """A throwaway copy of the project that shares its node_modules."""
    with tempfile.TemporaryDirectory(prefix="regress-eval-") as tmp:
        target = Path(tmp) / root.name
        shutil.copytree(root, target, ignore=shutil.ignore_patterns("node_modules", ".regress", ".stryker-tmp", ".git"))
        if (root / "node_modules").is_dir():
            (target / "node_modules").symlink_to((root / "node_modules").resolve(), target_is_directory=True)
        yield target.resolve()


def hidden_bugs_caught(
    project: Project, suite: BugSuite, label: str, content: str | None, output_dir: Path
) -> StageResult:
    """Run a test file against each seeded bug; a bug is caught when any test fails."""
    stage = StageResult(label=label)
    if content is None:
        stage.missed = [bug.id for bug in suite.bugs]
        return stage
    workspace = Workspace(project)
    original = project.source.read_text()
    try:
        workspace.write_tests(content)
        clean = run_vitest(project, output_dir, f"{suite.name}-{_slug(label)}-clean")
        if not workspace.source_intact():
            raise RegressError(f"{label} tests for {suite.name} modified the original source.")
        if not clean.success:
            raise RegressError(f"{label} tests for {suite.name} fail on the original source.")
        stage.tests = clean.total
        for bug in suite.bugs:
            occurrences = original.count(bug.find)
            if occurrences != 1:
                raise RegressError(f"Hidden bug {bug.id}: `find` text occurs {occurrences} times in {suite.source}.")
            atomic_write_text(project.source, original.replace(bug.find, bug.replace))
            result = run_vitest(project, output_dir, f"{suite.name}-{_slug(label)}-{bug.id}")
            (stage.missed if result.success else stage.caught).append(bug.id)
    finally:
        try:
            workspace.source_intact()
        finally:
            workspace.restore()
    return stage


def evaluate_regress(
    examples_dir: Path,
    suites: list[BugSuite],
    llm_factory: Callable[[], LLM],
    options: RunOptions,
    reporter: Reporter,
    runner: Runner = "auto",
    on_module: Callable[[BugSuite], None] | None = None,
) -> EvalResult:
    """Existing tests vs one-shot AI tests vs Regress, per module."""
    options = replace(options, measure_baseline=True, generate=True)
    out_dir = _output_dir(examples_dir)
    llm = llm_factory()
    result = EvalResult(created_at=datetime.now().astimezone(), mode="regress", model=llm.model, rounds=options.rounds)
    try:
        for suite in suites:
            if on_module:
                on_module(suite)
            _evaluate_module(examples_dir, suite, llm, options, reporter, runner, result, out_dir)
    finally:
        try:
            _save(result, out_dir)  # also when interrupted, so finished modules are not lost
        finally:
            close = getattr(llm, "close", None)
            if callable(close):
                close()
    return result


def _evaluate_module(
    examples_dir: Path,
    suite: BugSuite,
    llm: LLM,
    options: RunOptions,
    reporter: Reporter,
    runner: Runner,
    result: EvalResult,
    out_dir: Path,
) -> None:
    module = ModuleResult(module=suite.name)
    result.modules.append(module)
    with sandbox(examples_dir) as root:
        project = load_project(root / suite.source, root / suite.test, runner)
        store = RunStore(root)
        rejected: CandidateRejected | None = None
        try:
            Pipeline(project, llm, options, reporter, store).run()
        except CandidateRejected as error:
            rejected = error  # an outcome of the approach under test, not a failed measurement
        except RegressError as error:
            module.error = str(error)
            return
        finally:
            # Copy the run out of the throwaway sandbox even when it failed or was interrupted.
            _keep_artifacts(store, out_dir / suite.name)

        report, run_dir = store.load()
        module.run_id = report.id
        try:
            existing = _hidden_bugs_for_stage(project, suite, "Existing tests", report.stage("baseline"), run_dir)
            module.stages.append(existing)
            if rejected is not None:
                # No valid test file, so the run kept the existing tests. Leaving the module out would
                # total only the modules the model managed, which flatters both AI columns.
                module.stages += [existing.model_copy(update={"label": label}, deep=True) for label in AI_LABELS]
                module.note = f"{rejected} One-shot AI and Regress count as the existing tests, which the run kept."
            else:
                for label, stage in zip(AI_LABELS, (report.stage("generated"), report.kept), strict=True):
                    module.stages.append(_hidden_bugs_for_stage(project, suite, label, stage, run_dir))
        except RegressError as error:
            module.error = str(error)
        _keep_artifacts(store, out_dir / suite.name)


def _hidden_bugs_for_stage(
    project: Project, suite: BugSuite, label: str, stage: Stage | None, run_dir: Path
) -> StageResult:
    """Hidden bugs caught by a stage's saved test file; a stage without one catches none."""
    content = None
    if stage is not None and stage.test_file_snapshot:
        snapshot = (run_dir / stage.test_file_snapshot).resolve()
        if not snapshot.is_relative_to(run_dir.resolve()):
            raise RegressError("The saved test snapshot is outside the run directory.")
        try:
            content = snapshot.read_text(encoding="utf-8")
        except (OSError, ValueError) as error:
            raise RegressError(f"Cannot read the saved test snapshot: {error}") from error
    outcome = hidden_bugs_caught(project, suite, label, content, run_dir / "hidden-bugs")
    outcome.score = 0.0 if content is None else stage.score if stage else None
    outcome.tests = stage.test_count if stage else 0
    return outcome


def evaluate_oracles(
    examples_dir: Path,
    suites: list[BugSuite],
    runner: Runner = "auto",
    measure_mutation: bool = True,
    on_module: Callable[[BugSuite], None] | None = None,
) -> EvalResult:
    """Check the seeded bugs with hand-written reference tests; no model involved.

    A seeded bug that the oracle cannot catch is not a meaningful hidden bug, and the oracle's
    mutation score shows what a thorough suite achieves on each module.
    """
    result = EvalResult(created_at=datetime.now().astimezone(), mode="oracle")
    out_dir = _output_dir(examples_dir)
    try:
        for suite in suites:
            if on_module:
                on_module(suite)
            module = ModuleResult(module=suite.name)
            result.modules.append(module)
            if not suite.oracle:
                module.error = "No oracle test file configured."
                continue
            with sandbox(examples_dir) as root:
                project = load_project(root / suite.source, root / suite.test, runner)
                existing = project.test_file.read_text() if project.test_file.is_file() else None
                oracle = (root / suite.oracle).read_text()
                work = root / ".regress" / "oracle"
                try:
                    for label, content in (("Existing tests", existing), ("Oracle", oracle)):
                        stage = hidden_bugs_caught(project, suite, label, content, work)
                        if measure_mutation and content is not None:
                            workspace = Workspace(project)
                            workspace.write_tests(content)
                            try:
                                stage.score = run_stryker(project, work, len(module.stages) + 1).score
                            finally:
                                try:
                                    workspace.source_intact()
                                finally:
                                    workspace.restore()
                        elif content is None:
                            stage.score = 0.0
                        module.stages.append(stage)
                except RegressError as error:
                    module.error = str(error)
    finally:
        _save(result, out_dir)
    return result


def to_markdown(result: EvalResult) -> str:
    labels = result.stage_labels
    header = "| Module | " + " | ".join(f"{label} score | {label} bugs" for label in labels) + " |"
    divider = "|---|" + "---:|---:|" * len(labels)
    rows = []
    for module in result.modules:
        cells = []
        for label in labels:
            stage = next((s for s in module.stages if s.label == label), None)
            if stage is None:
                cells += ["—", "—"]
            else:
                cells += [_pct(stage.score), f"{len(stage.caught)}/{stage.bugs_total}"]
        # Errors can span lines or contain pipes, either of which would break the table row.
        error = " ".join((module.error or "").split()).replace("|", "\\|")
        suffix = f" ⚠ {error}" if error else ""
        rows.append(f"| {module.module}{suffix} | " + " | ".join(cells) + " |")
    totals = []
    for total in result.totals:
        totals += [f"**{_pct(total.score)}**", f"**{total.caught}/{total.bugs_total}**"]
    rows.append(f"| **{result.totals_label}** | " + " | ".join(totals) + " |")
    notes = [f"- **{m.module}:** {m.note}" for m in result.modules if m.note]
    if len(result.counted_modules) < len(result.modules):
        notes.append(f"- {COUNTING_NOTE}")
    title = f"# Regress evaluation ({result.mode})\n\n"
    meta = f"Model: `{result.model}` · rounds: {result.rounds}\n\n" if result.model else ""
    table = "\n".join([header, divider, *rows]) + "\n"
    return title + meta + table + ("\n" + "\n".join(notes) + "\n" if notes else "")


def _pct(score: float | None) -> str:
    return "—" if score is None else f"{score:.0f}%"


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")


def _output_dir(examples_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = regress_dir(examples_dir) / "eval"
    _check_artifact_path(examples_dir, base)
    base.mkdir(parents=True, exist_ok=True)
    path = base / stamp
    suffix = 1
    while True:
        try:
            path.mkdir()
            return path
        except FileExistsError:
            suffix += 1
            path = base / f"{stamp}-{suffix}"


def _keep_artifacts(store: RunStore, destination: Path) -> None:
    runs = store.run_dirs()
    if runs:
        shutil.copytree(runs[-1], destination, dirs_exist_ok=True)


def _save(result: EvalResult, out_dir: Path) -> None:
    result.output_dir = str(out_dir)
    atomic_write_text(out_dir / "eval.json", result.model_dump_json(indent=2))
    atomic_write_text(out_dir / "eval.md", to_markdown(result))
