from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from regress.errors import CandidateRejected, RegressError
from regress.llm import LLM, Completion
from regress.models import RunReport, Stage, StageKind
from regress.mutants import describe_mutant, select_mutants
from regress.project import Project
from regress.prompts import INSTRUCTIONS, PromptContext, generate_prompt, improve_prompt, repair_prompt
from regress.store import RunStore
from regress.stryker import run_stryker
from regress.ui import Reporter
from regress.validate import Workspace, check_candidate, clean_test_file
from regress.vitest import run_vitest


@dataclass
class RunOptions:
    rounds: int = 1
    max_repairs: int = 2
    max_mutants: int = 40
    measure_baseline: bool = False
    generate: bool = True
    vitest_timeout: float = 300
    stryker_timeout: float = 1800


@dataclass
class Version:
    """A test file's content together with what we measured about it."""

    stage: Stage
    content: str | None

    @property
    def score(self) -> float:
        return self.stage.score if self.stage.score is not None else -1.0


class Pipeline:
    def __init__(
        self,
        project: Project,
        llm: LLM,
        options: RunOptions | None = None,
        reporter: Reporter | None = None,
        store: RunStore | None = None,
    ) -> None:
        self.project = project
        self.llm = llm
        self.options = options or RunOptions()
        self.reporter = reporter or Reporter()
        self.store = store or RunStore(project.root)
        self.ctx = PromptContext.from_project(project)
        self.source_lines = project.source.read_text().splitlines(keepends=True)
        self._mutation_runs = 0
        self._llm_calls = 0

    def run(self, run_dir: Path | None = None) -> RunReport:
        """Run the whole loop. `run_dir` is an empty directory created in advance, e.g. to hand out the ID early."""
        started = time.monotonic()
        self.run_dir = run_dir or self.store.create(self.project.source)
        self.workspace = Workspace(self.project)
        self.report = RunReport(
            id=self.run_dir.name,
            created_at=datetime.now().astimezone(),
            project_root=str(self.project.root),
            source_file=self.project.source_rel,
            test_file=self.project.test_rel,
            test_file_existed=self.workspace.original_tests is not None,
            model=self.llm.model,
        )
        self.reporter.start(self.project, self.report)
        kept: Version | None = None
        try:
            kept = self._run()
            self.report.status = "completed"
        except BaseException as error:
            self.report.status = "cancelled" if isinstance(error, KeyboardInterrupt) else "failed"
            if isinstance(error, KeyboardInterrupt):
                self.report.error = "Interrupted"
            elif isinstance(error, RegressError):
                self.report.error = str(error)
            else:
                self.report.error = f"Unexpected {type(error).__name__}: {error}"
            raise
        finally:
            self._finalize(kept)
            self.report.duration_seconds = round(time.monotonic() - started, 1)
            self.store.save(self.report, self.run_dir)
        self.reporter.finish(self.report, self.run_dir)
        return self.report

    def _run(self) -> Version:
        baseline = self._baseline()
        if self.options.generate:
            current = self._propose(
                "generated", "Generated tests", generate_prompt(self.ctx, baseline.content), baseline
            )
            self.reporter.generated(baseline.stage, current.stage)
            self._mutate(current)
        else:
            if baseline.content is None or baseline.stage.test_count == 0:
                raise RegressError("--no-generate needs an existing test file with at least one test to improve.")
            current = baseline
            if current.stage.mutation is None:
                self._mutate(current)

        equivalent: set[str] = set()
        for round_number in range(1, self.options.rounds + 1):
            assert current.stage.mutation is not None
            mutants = select_mutants(current.stage.mutation, self.options.max_mutants, equivalent)
            if not mutants:
                self.reporter.note("No undetected mutants left to improve on.")
                break
            self.reporter.improving(round_number, len(mutants), len(current.stage.mutation.undetected))
            descriptions = [describe_mutant(m, self.source_lines, self.project.source_rel) for m in mutants]
            prompt = improve_prompt(self.ctx, current.content or "", current.stage.test_count, descriptions)
            label = "Improved tests" if self.options.rounds == 1 else f"Improved tests (round {round_number})"
            try:
                improved = self._propose("improved", label, prompt, current, targeted=[m.id for m in mutants])
            except CandidateRejected as error:
                self.reporter.warn(f"{error} Keeping the previous tests.")
                self.report.stages.append(Stage(kind="improved", label=label, rejected=True, problems=error.problems))
                break
            equivalent |= set(improved.stage.equivalent_mutants)
            self.reporter.improved(current.stage, improved.stage)
            self._mutate(improved)
            if improved.score >= current.score:
                current = improved
            else:
                self.reporter.warn("The mutation score went down; keeping the previous tests.")
        return current

    def _baseline(self) -> Version:
        stage = Stage(kind="baseline", label="Existing tests")
        content = self.workspace.original_tests
        if content is not None:
            with self.reporter.activity("Running existing tests"):
                result = run_vitest(self.project, self.run_dir / "vitest", "baseline", self.options.vitest_timeout)
            if result.total > 0 and not result.success:
                failing = [t.full_name for t in result.failed][:10] or (result.suite_errors + result.run_errors)[:1]
                raise RegressError(
                    f"The existing tests in {self.project.test_rel} do not pass. Fix them first.\n"
                    + "\n".join(f"  - {item}" for item in failing)
                )
            stage.test_count = result.total
            stage.test_names = sorted(result.names)
        self.report.stages.append(stage)
        version = Version(stage, content)
        self._snapshot(version)
        self.reporter.baseline(stage, exists=content is not None)
        if content is not None and stage.test_count > 0 and self.options.measure_baseline:
            self._mutate(version)
        return version

    def _propose(
        self, kind: StageKind, label: str, task_prompt: str, previous: Version, targeted: list[str] | None = None
    ) -> Version:
        prompt, problems = task_prompt, []
        attempts = self.options.max_repairs + 1
        verb = "Generating tests" if kind == "generated" else "Improving tests"
        llm_seconds = 0.0
        for attempt in range(1, attempts + 1):
            suffix = f" (attempt {attempt}/{attempts})" if attempt > 1 else ""
            started = time.monotonic()
            with self.reporter.activity(f"{verb} with {self.llm.model}{suffix}") as status:
                completion = self.llm.propose(INSTRUCTIONS, prompt, on_status=status, on_warning=self.reporter.warn)
            llm_seconds += time.monotonic() - started
            self.report.usage.add(completion.input_tokens, completion.output_tokens)
            content = clean_test_file(completion.proposal.test_file)
            name = self._log_llm(kind, attempt, prompt, completion)
            with self.reporter.activity("Checking the new tests against the original code"):
                check = check_candidate(
                    self.project,
                    self.workspace,
                    content,
                    previous=previous.content,
                    required_tests=set(previous.stage.test_names),
                    min_tests=previous.stage.test_count + 1,
                    output_dir=self.run_dir / "vitest",
                    name=name,
                    timeout=self.options.vitest_timeout,
                )
            if check.ok and check.result is not None:
                proposal = completion.proposal
                targeted = targeted or []
                stage = Stage(
                    kind=kind,
                    label=label,
                    test_count=check.result.total,
                    test_names=sorted(check.result.names),
                    summary=proposal.summary,
                    attempts=attempt,
                    llm_seconds=round(llm_seconds, 1),
                    targeted_mutants=targeted,
                    equivalent_mutants=[i for i in proposal.equivalent_mutants if i in targeted],
                )
                self.report.stages.append(stage)
                version = Version(stage, content)
                self._snapshot(version)
                return version
            problems = check.problems
            self.reporter.rejected(attempt, attempts, problems)
            prompt = repair_prompt(task_prompt, self.ctx.language, content, problems)
        self.workspace.write_tests(previous.content)
        raise CandidateRejected(f"The model did not produce valid tests in {attempts} attempts.", problems)

    def _mutate(self, version: Version) -> None:
        self.workspace.write_tests(version.content)
        self._mutation_runs += 1
        with self.reporter.activity(f"Running mutation testing on {self.project.source_rel}"):
            run = run_stryker(self.project, self.run_dir / "stryker", self._mutation_runs, self.options.stryker_timeout)
        if not self.workspace.source_intact():
            raise RegressError("The source file changed during mutation testing; it has been restored.")
        version.stage.mutation = run
        self.reporter.mutation(version.stage)
        covered = [m for m in run.undetected if m.covered_by]
        if run.killed == 0 and len(covered) >= 3 and version.stage.test_count > 0:
            self.reporter.warn(
                "No mutant was killed even though the tests execute the mutated code. Either the tests make "
                "no real assertions or Stryker is not activating mutants (check your Vitest version)."
            )

    def _finalize(self, kept: Version | None) -> None:
        if kept is None:
            self.workspace.restore()
            self.report.kept_stage = None
            return
        self.workspace.write_tests(kept.content)
        self.report.kept_stage = kept.stage.label

    def _snapshot(self, version: Version) -> None:
        if version.content is None:
            return
        directory = self.run_dir / "tests"
        directory.mkdir(exist_ok=True)
        index = len(list(directory.iterdir()))
        path = directory / f"{index}-{version.stage.kind}{_test_suffix(self.project.test_file)}"
        path.write_text(version.content)
        version.stage.test_file_snapshot = path.relative_to(self.run_dir).as_posix()

    def _log_llm(self, kind: str, attempt: int, prompt: str, completion: Completion) -> str:
        self._llm_calls += 1
        name = f"{self._llm_calls:02d}-{kind}-attempt{attempt}"
        directory = self.run_dir / "llm"
        directory.mkdir(exist_ok=True)
        (directory / f"{name}.prompt.md").write_text(f"<!-- instructions -->\n{INSTRUCTIONS}\n<!-- input -->\n{prompt}")
        payload = {
            **completion.proposal.model_dump(),
            "usage": {"input_tokens": completion.input_tokens, "output_tokens": completion.output_tokens},
        }
        (directory / f"{name}.response.json").write_text(json.dumps(payload, indent=2))
        return name


def _test_suffix(path: Path) -> str:
    """`.test.ts` for `cart.test.ts`: everything after the stem's first dot."""
    name = path.name
    return name[name.index(".") :] if "." in name else path.suffix
