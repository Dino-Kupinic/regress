"""Request and response bodies. They make up the OpenAPI schema the web app's types are generated from."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from regress.catalog import Source as CatalogSource
from regress.config import ModelSource, Settings
from regress.models import Mutant, RunReport, RunStatus, StageKind
from regress.project import Runner


class ErrorBody(BaseModel):
    detail: str
    output: str | None = Field(default=None, description="Tool output that explains the error, e.g. from Stryker.")


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    project_root: str


# --- project ---------------------------------------------------------------------------------


class PackageStatus(BaseModel):
    name: str
    version: str | None = Field(description="Installed version, or null when the package is missing.")


class Toolchain(BaseModel):
    runner: Literal["bun", "npx"]
    vitest: str
    stryker: str
    vitest_runner: str


class ProjectInfo(BaseModel):
    root: str
    name: str = Field(description="The name in package.json, else the directory name.")
    config_file: str | None = Field(description="regress.toml, when the project has one.")
    packages: list[PackageStatus]
    toolchain: Toolchain | None = Field(description="How Vitest and Stryker are run; null while something is missing.")
    problems: list[str] = Field(description="What stops a run from starting. Empty when the project is ready.")
    ready: bool
    api_key_set: bool
    active_run: str | None = Field(description="ID of the run in progress, if any.")


class InitResult(BaseModel):
    installed: list[str] = Field(description="Package specs that were installed.")
    command: list[str] | None = Field(description="The install command that ran, if anything was missing.")
    config_written: bool = Field(description="Whether regress.toml was created.")
    project: ProjectInfo


class SourceFile(BaseModel):
    path: str = Field(description="Relative to the project root.")
    size: int


class SourceDetail(BaseModel):
    path: str
    test_file: str = Field(description="The test file a run would extend, found the same way as `regress run`.")
    test_file_exists: bool = Field(description="False when a run would create the test file.")
    import_path: str = Field(description="How the test file imports the source.")
    lines: int


class FileContent(BaseModel):
    path: str
    content: str


# --- settings and models ---------------------------------------------------------------------


class SettingsInfo(BaseModel):
    effective: Settings = Field(description="The settings a run uses unless the request overrides them.")
    model_source: ModelSource = Field(description="Which layer chose the model.")
    user_config_path: str
    user_config: dict[str, Any] = Field(description="The keys set in your user config.")
    project_config_path: str | None = Field(description="The project's regress.toml, which overrides your user config.")


SettingsUpdate = create_model(
    "SettingsUpdate",
    __config__=ConfigDict(extra="forbid"),
    __doc__="Keys to change in your user config. Omitted keys stay as they are; null removes a key.",
    **{name: (field.annotation | None, None) for name, field in Settings.model_fields.items()},
)


class ModelEntry(BaseModel):
    id: str
    created: int | None = Field(description="Unix time the model was released, when the API says.")
    created_date: str
    shutdown_date: str | None
    newest: bool
    default: bool


class ModelList(BaseModel):
    models: list[ModelEntry] = Field(description="Newest first; the default model is always included.")
    source: CatalogSource = Field(description="api: fetched now; cache: fetched earlier today; sdk: offline fallback.")
    description: str
    note: str | None = Field(description="Why the list may be out of date.")
    verified: bool = Field(description="Whether the list reflects what the API key can use.")
    default_model: str
    model_source: ModelSource
    hidden: int = Field(description="Models left out of this list (older ones and dated snapshots).")


# --- runs ------------------------------------------------------------------------------------


class RunRequest(BaseModel):
    """What `regress run` takes on the command line."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(description="Source file to test, relative to the project root, e.g. src/cart.ts.")
    test: str | None = Field(default=None, description="Test file to extend. Found automatically by default.")
    model: str | None = Field(default=None, description="OpenAI model. Defaults to the configured model.")
    rounds: int | None = Field(default=None, ge=0, le=5, description="Improvement rounds after the first mutation run.")
    baseline: bool = Field(default=False, description="Also mutation-test the existing tests first.")
    generate: bool = Field(
        default=True, description="Start with a one-shot generation round; false improves the existing tests directly."
    )
    runner: Runner | None = Field(default=None, description="How to run Vitest and Stryker.")


class RunSummary(BaseModel):
    id: str
    created_at: datetime
    source_file: str
    test_file: str
    model: str
    status: RunStatus
    error: str | None
    kept_stage: str | None
    duration_seconds: float
    baseline_score: float | None = Field(description="Mutation score of the existing tests, if measured.")
    reference_score: float | None = Field(description="The score improvements are measured against.")
    kept_score: float | None = Field(description="Mutation score of the tests left in the test file.")
    improvement: float | None = Field(description="kept_score - reference_score, in percentage points.")
    tests_before: int
    tests_after: int | None
    active: bool


class LiveState(BaseModel):
    """What a run in progress is doing right now."""

    started_at: datetime
    elapsed_seconds: float
    activity: str | None = Field(
        description='The slow step in progress, e.g. "Running mutation testing on src/cart.ts".'
    )
    activity_status: str | None = Field(
        description='Finer progress within it, e.g. "thinking" or "writing ~1,200 tokens".'
    )
    activity_started_at: datetime | None
    cancel_requested: bool
    last_event: int = Field(description="Sequence number of the latest event.")


class RunDetail(BaseModel):
    summary: RunSummary
    report: RunReport
    live: LiveState | None = Field(description="Only while the run is in progress.")


EventType = Literal[
    "start",
    "activity",
    "baseline",
    "generated",
    "rejected",
    "mutation",
    "improving",
    "improved",
    "warning",
    "note",
    "finished",
    "error",
    "cancelling",
    "cancelled",
]


class RunEvent(BaseModel):
    seq: int = Field(description="1-based position in the run's log.")
    time: datetime
    type: EventType
    message: str = Field(description="A one-line description, ready to show.")
    data: dict[str, Any] = Field(default_factory=dict)


class MutantView(Mutant):
    detected: bool = Field(description="Killed, or timed out, which Stryker also counts as detected.")
    equivalent: bool = Field(description="The model flagged this mutant as likely equivalent to the original code.")
    original_line: str | None = Field(description="The source line before the change (single-line mutants only).")
    mutated_line: str | None = Field(description="The same line with the mutation applied.")
    summary: str = Field(description="One line: original → mutated.")


class StageMutants(BaseModel):
    stage: int
    label: str
    source_file: str
    total: int = Field(description="All mutants of this mutation run, before filtering.")
    mutants: list[MutantView]


class StageTestFile(BaseModel):
    stage: int
    label: str
    kind: StageKind
    test_file: str
    test_count: int
    content: str


class StageDiff(BaseModel):
    stage: int
    label: str
    before_stage: int | None = Field(description="The stage whose tests this one replaced; null for the original file.")
    before_label: str | None
    test_file: str
    before: str = Field(description="Empty when there was no test file yet.")
    after: str
    unified: str = Field(description="A unified diff of before and after.")
    added_tests: list[str]


class LLMCall(BaseModel):
    name: str
    kind: StageKind
    attempt: int
    input_tokens: int
    output_tokens: int
    summary: str
    new_tests: list[str]
    equivalent_mutants: list[str]


class LLMCallDetail(LLMCall):
    prompt: str = Field(description="The instructions and input sent to the model.")
    test_file: str = Field(description="The test file the model returned, before validation.")


class Artifact(BaseModel):
    path: str = Field(description="Relative to the run directory.")
    size: int
