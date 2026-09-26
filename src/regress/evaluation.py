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
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from regress.errors import RegressError
from regress.llm import LLM
from regress.pipeline import Pipeline, RunOptions
from regress.project import Project, Runner, load_project
from regress.store import RunStore, regress_dir
from regress.stryker import run_stryker
from regress.ui import Reporter
from regress.validate import Workspace
from regress.vitest import run_vitest

BUGS_DIR = "hidden-bugs"


class HiddenBug(BaseModel):
    id: str
    description: str
    find: str
    replace: str


class BugSuite(BaseModel):
    name: str = ""
    source: str
    test: str
    oracle: str | None = None
    bugs: list[HiddenBug]


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
    run_id: str | None = None


class EvalResult(BaseModel):
    created_at: datetime
    mode: str
    model: str | None = None
    rounds: int | None = None
    modules: list[ModuleResult] = Field(default_factory=list)

    @property
    def stage_labels(self) -> list[str]:
        labels: list[str] = []
        for module in self.modules:
            labels += [s.label for s in module.stages if s.label not in labels]
        return labels

    def totals(self, label: str) -> tuple[float | None, int, int]:
        """Mean mutation score, bugs caught, and bugs total for one stage across modules."""
        stages = [s for m in self.modules for s in m.stages if s.label == label]
        scores = [s.score for s in stages if s.score is not None]
        mean = sum(scores) / len(scores) if scores else None
        return mean, sum(len(s.caught) for s in stages), sum(s.bugs_total for s in stages)


def load_suites(examples_dir: Path, names: list[str] | None = None) -> list[BugSuite]:
    directory = examples_dir / BUGS_DIR
    if not directory.is_dir():
        raise RegressError(f"No {BUGS_DIR}/ directory in {examples_dir}.")
    suites = []
    for path in sorted(directory.glob("*.json")):
        suite = BugSuite.model_validate_json(path.read_text())
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
    workspace.write_tests(content)
    try:
        clean = run_vitest(project, output_dir, f"{suite.name}-{_slug(label)}-clean")
        if not clean.success:
            raise RegressError(f"{label} tests for {suite.name} fail on the original source.")
        stage.tests = clean.total
        for bug in suite.bugs:
            occurrences = original.count(bug.find)
            if occurrences != 1:
                raise RegressError(f"Hidden bug {bug.id}: `find` text occurs {occurrences} times in {suite.source}.")
            project.source.write_text(original.replace(bug.find, bug.replace))
            result = run_vitest(project, output_dir, f"{suite.name}-{_slug(label)}-{bug.id}")
            (stage.missed if result.success else stage.caught).append(bug.id)
    finally:
        project.source.write_text(original)
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
    llm = llm_factory()
    options.measure_baseline = True
    options.generate = True
    result = EvalResult(created_at=datetime.now().astimezone(), mode="regress", model=llm.model, rounds=options.rounds)
    out_dir = _output_dir(examples_dir)
    for suite in suites:
        if on_module:
            on_module(suite)
        module = ModuleResult(module=suite.name)
        result.modules.append(module)
        with sandbox(examples_dir) as root:
            project = load_project(root / suite.source, root / suite.test, runner)
            store = RunStore(root)
            try:
                report = Pipeline(project, llm, options, reporter, store).run()
            except RegressError as error:
                module.error = str(error)
                _keep_artifacts(store, out_dir / suite.name)
                continue
            run_dir = store.run_dirs()[-1]
            module.run_id = report.id
            wanted = [
                ("Existing tests", report.stage("baseline")),
                ("One-shot AI", report.stage("generated")),
                ("Regress", report.kept),
            ]
            try:
                for label, stage in wanted:
                    content = None
                    if stage is not None and stage.test_file_snapshot:
                        content = (run_dir / stage.test_file_snapshot).read_text()
                    outcome = hidden_bugs_caught(project, suite, label, content, run_dir / "hidden-bugs")
                    outcome.score = 0.0 if content is None else stage.score if stage else None
                    outcome.tests = stage.test_count if stage else 0
                    module.stages.append(outcome)
            except RegressError as error:
                module.error = str(error)
            _keep_artifacts(store, out_dir / suite.name)
    _save(result, out_dir)
    return result


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
                            workspace.restore()
                    elif content is None:
                        stage.score = 0.0
                    module.stages.append(stage)
            except RegressError as error:
                module.error = str(error)
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
        suffix = f" ⚠ {module.error}" if module.error else ""
        rows.append(f"| {module.module}{suffix} | " + " | ".join(cells) + " |")
    totals = []
    for label in labels:
        mean, caught, total = result.totals(label)
        totals += [f"**{_pct(mean)}**", f"**{caught}/{total}**"]
    rows.append("| **All modules** | " + " | ".join(totals) + " |")
    title = f"# Regress evaluation ({result.mode})\n\n"
    meta = f"Model: `{result.model}` · rounds: {result.rounds}\n\n" if result.model else ""
    return title + meta + "\n".join([header, divider, *rows]) + "\n"


def _pct(score: float | None) -> str:
    return "—" if score is None else f"{score:.0f}%"


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")


def _output_dir(examples_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = regress_dir(examples_dir) / "eval" / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def _keep_artifacts(store: RunStore, destination: Path) -> None:
    runs = store.run_dirs()
    if runs:
        shutil.copytree(runs[-1], destination, dirs_exist_ok=True)


def _save(result: EvalResult, out_dir: Path) -> None:
    (out_dir / "eval.json").write_text(result.model_dump_json(indent=2))
    (out_dir / "eval.md").write_text(to_markdown(result))
