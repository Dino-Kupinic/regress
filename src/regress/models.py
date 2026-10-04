from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, computed_field


class TestStatus(StrEnum):
    __test__ = False  # not a pytest test class

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    PENDING = "pending"
    TODO = "todo"


class TestCase(BaseModel):
    __test__ = False  # not a pytest test class

    full_name: str
    status: TestStatus
    failure_messages: list[str] = Field(default_factory=list)


class TestRunResult(BaseModel):
    """Outcome of running a single test file with Vitest."""

    __test__ = False

    success: bool
    tests: list[TestCase] = Field(default_factory=list)
    suite_errors: list[str] = Field(default_factory=list)
    # Failures outside any single test (a throwing beforeAll/afterAll, an unhandled rejection):
    # every test can pass while the run as a whole fails.
    run_errors: list[str] = Field(default_factory=list)
    output: str = ""

    @property
    def total(self) -> int:
        return len(self.tests)

    @property
    def failed(self) -> list[TestCase]:
        return [t for t in self.tests if t.status == TestStatus.FAILED]

    @property
    def skipped(self) -> list[TestCase]:
        return [t for t in self.tests if t.status in (TestStatus.SKIPPED, TestStatus.PENDING, TestStatus.TODO)]

    @property
    def names(self) -> set[str]:
        return {t.full_name for t in self.tests}


class MutantStatus(StrEnum):
    KILLED = "Killed"
    SURVIVED = "Survived"
    NO_COVERAGE = "NoCoverage"
    TIMEOUT = "Timeout"
    COMPILE_ERROR = "CompileError"
    RUNTIME_ERROR = "RuntimeError"
    IGNORED = "Ignored"
    PENDING = "Pending"


class Mutant(BaseModel):
    id: str
    mutator: str
    status: MutantStatus
    replacement: str
    original: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    killed_by: list[str] = Field(default_factory=list)
    covered_by: list[str] = Field(default_factory=list)
    status_reason: str | None = None

    @property
    def undetected(self) -> bool:
        return self.status in (MutantStatus.SURVIVED, MutantStatus.NO_COVERAGE)


class MutationRun(BaseModel):
    """Result of one Stryker run against the target source file."""

    index: int
    mutants: list[Mutant]
    duration_seconds: float = 0.0

    def _count(self, *statuses: MutantStatus) -> int:
        return sum(1 for m in self.mutants if m.status in statuses)

    @computed_field
    @property
    def killed(self) -> int:
        return self._count(MutantStatus.KILLED)

    @computed_field
    @property
    def survived(self) -> int:
        return self._count(MutantStatus.SURVIVED)

    @computed_field
    @property
    def no_coverage(self) -> int:
        return self._count(MutantStatus.NO_COVERAGE)

    @computed_field
    @property
    def timeout(self) -> int:
        return self._count(MutantStatus.TIMEOUT)

    @computed_field
    @property
    def errors(self) -> int:
        return self._count(MutantStatus.COMPILE_ERROR, MutantStatus.RUNTIME_ERROR)

    @computed_field
    @property
    def score(self) -> float:
        """Stryker's mutation score: detected / (detected + undetected), in percent."""
        detected = self.killed + self.timeout
        valid = detected + self.survived + self.no_coverage
        return 100.0 * detected / valid if valid else 0.0

    @property
    def undetected(self) -> list[Mutant]:
        return [m for m in self.mutants if m.undetected]


StageKind = Literal["baseline", "generated", "improved"]
RunStatus = Literal["running", "completed", "failed", "cancelled"]


class Stage(BaseModel):
    """One version of the test file and what we learned about it."""

    kind: StageKind
    label: str
    test_file_snapshot: str | None = None
    test_count: int = 0
    test_names: list[str] = Field(default_factory=list)
    mutation: MutationRun | None = None
    summary: str | None = None
    attempts: int = 0
    llm_seconds: float = 0.0
    equivalent_mutants: list[str] = Field(default_factory=list)
    targeted_mutants: list[str] = Field(default_factory=list)
    rejected: bool = False
    problems: list[str] = Field(default_factory=list)

    @property
    def score(self) -> float | None:
        return self.mutation.score if self.mutation else None


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.calls += 1


class RunReport(BaseModel):
    id: str
    created_at: datetime
    project_root: str
    source_file: str
    test_file: str
    test_file_existed: bool
    model: str
    provider: str = "openai"
    # Source lines Stryker mutated, 1-based and inclusive; empty when it mutated the whole file.
    mutate_lines: list[tuple[int, int]] = Field(default_factory=list)
    batch_id: str | None = None
    status: RunStatus = "running"
    error: str | None = None
    stages: list[Stage] = Field(default_factory=list)
    kept_stage: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    duration_seconds: float = 0.0

    def stage(self, kind: StageKind) -> Stage | None:
        return next((s for s in self.stages if s.kind == kind and not s.rejected), None)

    @property
    def measured(self) -> list[Stage]:
        return [s for s in self.stages if s.mutation is not None and not s.rejected]

    @property
    def kept(self) -> Stage | None:
        return next((s for s in self.stages if s.label == self.kept_stage), None)

    @property
    def reference(self) -> Stage | None:
        """The stage improvements are measured against: the one-shot tests, else the existing ones."""
        for kind in ("generated", "baseline"):
            stage = self.stage(kind)
            if stage is not None and stage.mutation is not None:
                return stage
        return None

    @property
    def improvement(self) -> float | None:
        reference, kept = self.reference, self.kept
        if reference is None or kept is None or kept.score is None:
            return None
        return kept.score - reference.score
