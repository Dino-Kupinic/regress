"""HTTP routes, all under /api. The OpenAPI schema is at /api/openapi.json, interactive docs at /api/docs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from tempfile import TemporaryFile
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent

from regress import __version__
from regress.api import project as projects
from regress.api import results
from regress.api.evaluations import EvalJob, EvalRequest, EvaluationManager
from regress.api.jobs import RunHandle, RunManager
from regress.api.schemas import (
    Artifact,
    ErrorBody,
    FileContent,
    Health,
    InitResult,
    LLMCall,
    LLMCallDetail,
    ModelEntry,
    ModelList,
    ProjectInfo,
    Readiness,
    RunDetail,
    RunEvent,
    RunRequest,
    RunSummary,
    SettingsInfo,
    SettingsUpdate,
    SourceDetail,
    SourceFile,
    StageDiff,
    StageMutants,
    StageTestFile,
)
from regress.catalog import ModelInfo, load_catalog
from regress.config import load_settings, save_user_settings
from regress.errors import ProjectError
from regress.evaluation import BugSuite, EvalResult
from regress.providers import ProviderName
from regress.ui import PICKER_SIZE

STREAM_INTERVAL = 0.25

router = APIRouter(
    prefix="/api",
    responses={"4XX": {"model": ErrorBody, "description": "Client error"}, "5XX": {"model": ErrorBody}},
)


def _manager(request: Request) -> RunManager:
    return request.app.state.manager


def _evaluations(request: Request) -> EvaluationManager:
    return request.app.state.evaluations


Manager = Annotated[RunManager, Depends(_manager)]
Evaluations = Annotated[EvaluationManager, Depends(_evaluations)]


@router.get("/evaluations", tags=["evaluation"])
def list_evaluations(evaluations: Evaluations) -> list[EvalResult]:
    return evaluations.results()


@router.get("/evaluations/suites", tags=["evaluation"])
def list_evaluation_suites(evaluations: Evaluations) -> list[BugSuite]:
    return evaluations.suites()


@router.get("/evaluations/job", tags=["evaluation"])
def get_evaluation_job(evaluations: Evaluations) -> EvalJob:
    return evaluations.job()


@router.post("/evaluations", status_code=202, tags=["evaluation"])
def start_evaluation(request: EvalRequest, evaluations: Evaluations, manager: Manager) -> EvalJob:
    if manager.active():
        raise HTTPException(409, "A run is in progress. Wait for it before starting an evaluation.")
    return evaluations.start(request.mode)


def _run(run_id: str, manager: Manager) -> RunHandle:
    return manager.lookup(run_id)


Run = Annotated[RunHandle, Depends(_run)]


@router.get("/health", tags=["meta"])
def health(manager: Manager) -> Health:
    return Health(version=__version__, project_root=str(manager.root))


@router.get("/ready", tags=["meta"], responses={503: {"model": Readiness}})
def readiness(manager: Manager, response: Response) -> Readiness:
    """Readiness for a new job: local configuration, installed tools, credentials and writable storage.

    Does not contact OpenAI or disclose project paths or configuration values.
    A busy worker remains ready; concurrent job submissions receive 409.
    """
    if manager.replacing:
        # The previous container is still shutting down. Report ready so the platform stops it
        # and this process can take the project lock.
        return Readiness(status="ready", project=True, storage=True)
    project_ready = projects.project_info(manager).ready
    storage_ready = True
    try:
        with TemporaryFile(dir=manager.root / ".regress") as probe:
            probe.write(b"ready")
            probe.flush()
    except OSError:
        storage_ready = False
    ready = project_ready and storage_ready
    response.status_code = 200 if ready else 503
    return Readiness(status="ready" if ready else "unavailable", project=project_ready, storage=storage_ready)


# --- project ---------------------------------------------------------------------------------


@router.get("/project", tags=["project"])
def get_project(manager: Manager) -> ProjectInfo:
    """The served project and whether a run can start: required packages, toolchain, API key."""
    return projects.project_info(manager)


@router.post("/project/init", tags=["project"])
def init_project(manager: Manager) -> InitResult:
    """Install what's missing (Vitest 4, StrykerJS) and write regress.toml, like `regress init --yes`.

    Holds the request until the install finishes (up to 10 minutes). Refused while a run is going.
    """
    return projects.init_project(manager)


@router.get("/project/sources", tags=["project"])
def list_sources(manager: Manager) -> list[SourceFile]:
    """Source files a run can target."""
    return projects.list_sources(manager.root)


@router.get("/project/sources/{path:path}", tags=["project"])
def get_source(path: str, manager: Manager) -> SourceDetail:
    """A source file and the test file a run on it would extend or create."""
    return projects.source_detail(manager, path)


@router.get("/project/files/{path:path}", tags=["project"])
def get_file(path: str, manager: Manager) -> FileContent:
    """The contents of a JavaScript or TypeScript file in the project, e.g. a source or its test file."""
    return projects.read_file(manager, path)


# --- settings and models ---------------------------------------------------------------------


@router.get("/settings", tags=["settings"])
def get_settings(manager: Manager) -> SettingsInfo:
    """The settings runs use, merged from defaults, your user config, regress.toml, and the environment."""
    return projects.settings_info(manager.root)


@router.patch("/settings", tags=["settings"])
def update_settings(update: SettingsUpdate, manager: Manager) -> SettingsInfo:  # type: ignore[valid-type]
    """Change your user config (~/.config/regress/config.toml), e.g. the default model.

    The project's regress.toml still wins over it; check `model_source` in the response.
    """
    changes = update.model_dump(exclude_unset=True)
    if "provider" in changes and "model" not in changes:
        changes["model"] = None  # the saved model belongs to the previous provider; use the new one's default
    try:
        save_user_settings(**changes)
    except ProjectError as error:
        raise HTTPException(422, str(error)) from error
    return projects.settings_info(manager.root)


@router.get("/models", tags=["settings"])
def list_models(
    manager: Manager,
    refresh: Annotated[bool, Query(description="Fetch the list again instead of using the day-old cache.")] = False,
    include_all: Annotated[bool, Query(alias="all", description="Include older models and dated snapshots.")] = False,
    provider: Annotated[
        ProviderName | None, Query(description="Another provider's models, e.g. before switching to it.")
    ] = None,
) -> ModelList:
    """The newest models the API key can use, for choosing which one writes the tests."""
    try:
        settings = load_settings(manager.root, provider=provider)
    except ProjectError as error:
        raise HTTPException(422, str(error)) from error
    catalog = load_catalog(
        refresh, provider=settings.provider, base_url=settings.base_url, api_key_env=settings.api_key_env
    )
    shown = catalog.latest(None if include_all else PICKER_SIZE, include_snapshots=include_all)
    if settings.model is not None and settings.model not in {m.id for m in shown}:
        shown.append(next((m for m in catalog.models if m.id == settings.model), ModelInfo(settings.model)))
    newest = catalog.latest(1)
    newest_id = newest[0].id if newest else None
    return ModelList(
        models=[
            ModelEntry(
                id=m.id,
                created=m.created,
                created_date=m.created_date,
                shutdown_date=m.shutdown_date,
                newest=m.id == newest_id,
                default=m.id == settings.model,
            )
            for m in shown
        ],
        source=catalog.source,
        description=catalog.describe(),
        note=catalog.note,
        verified=catalog.verified,
        provider=settings.provider,
        default_model=settings.model,
        model_source=settings.model_source,
        hidden=max(0, len(catalog.latest(None, include_snapshots=True)) - len(shown)),
    )


# --- runs ------------------------------------------------------------------------------------


@router.get("/runs", tags=["runs"])
def list_runs(
    manager: Manager,
    source: Annotated[str | None, Query(description="Only runs on this source file, e.g. src/cart.ts.")] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> list[RunSummary]:
    """All runs of the project, newest first, including the one in progress."""
    runs = [s for s in manager.summaries() if source is None or s.source_file == source]
    return runs[:limit]


@router.post("/runs", status_code=202, tags=["runs"])
def start_run(request: RunRequest, manager: Manager, response: Response, evaluations: Evaluations) -> RunDetail:
    """Start a run in the background and return at once. Follow it with the events stream or by polling.

    Only one run goes at a time: another start is refused with 409 until it is over.
    """
    if evaluations.job().status == "running":
        raise HTTPException(409, "An evaluation is in progress. Wait for it before starting a run.")
    job = manager.start(request)
    response.headers["Location"] = f"/api/runs/{job.id}"
    return _detail(manager.lookup(job.id))


@router.get("/runs/{run_id}", tags=["runs"])
def get_run(run: Run) -> RunDetail:
    """A run's report; while it is in progress, also what it is doing right now (`live`)."""
    return _detail(run)


@router.delete("/runs/{run_id}", status_code=204, tags=["runs"])
def delete_run(run_id: str, manager: Manager) -> None:
    """Delete a finished run's report and artifacts. The test file is not touched."""
    manager.delete(run_id)


@router.post("/runs/{run_id}/cancel", status_code=202, tags=["runs"])
def cancel_run(run: Run, manager: Manager) -> RunDetail:
    """Stop a run, like Ctrl-C in `regress run`: the test file is restored and the run ends as cancelled.

    Returns at once; the run is over when its status is no longer "running" (or the stream ends).
    """
    if run.job is None or run.job.done.is_set():
        raise HTTPException(409, f"Run {run.report.id} is not in progress (status: {run.report.status}).")
    run.job.cancel()
    return _detail(manager.lookup(run.report.id))


@router.get("/runs/{run_id}/events", tags=["runs"])
def list_run_events(
    run: Run,
    manager: Manager,
    after: Annotated[int, Query(ge=0, description="Only events after this sequence number.")] = 0,
) -> list[RunEvent]:
    """The run's log: what happened, in order. Poll with `after` set to the last `seq` you have."""
    return manager.events(run, after)


@router.get("/runs/{run_id}/events/stream", response_class=EventSourceResponse, tags=["runs"])
async def stream_run_events(
    run: Run,
    after: Annotated[int, Query(ge=0, description="Only events after this sequence number.")] = 0,
    last_event_id: Annotated[str | None, Header(include_in_schema=False)] = None,
) -> AsyncIterator[ServerSentEvent]:
    """Follow a run as server-sent events.

    - `run-event`: a new log entry (a RunEvent). Its `id` is the sequence number, so a reconnecting
      EventSource picks up where it left off.
    - `live`: what the run is doing now (a LiveState), whenever that changes.
    - `end`: the run's summary (a RunSummary) once it is over. The stream closes after it, so close
      the EventSource on `end`, or it reconnects.

    A finished run replays its log and ends straight away.
    """
    if last_event_id and last_event_id.isascii() and last_event_id.isdigit() and len(last_event_id) <= 20:
        after = int(last_event_id)
    job = run.job
    if job is None:
        for event in results.load_events(run.run_dir):
            if event.seq > after:
                yield _event(event)
        yield ServerSentEvent(event="end", data=results.summarize(run.report))
        return
    version = -1
    while True:
        events, live, current, done = job.since(after)
        for event in events:
            yield _event(event)
            after = event.seq
        if done:
            yield ServerSentEvent(event="end", data=job.summary())
            return
        if current != version:
            version = current
            yield ServerSentEvent(event="live", data=live)
        await asyncio.sleep(STREAM_INTERVAL)


@router.get("/runs/{run_id}/diff", tags=["runs"])
def get_run_diff(run: Run) -> StageDiff:
    """What the run changed in the test file: the original against the tests it kept."""
    return results.run_diff(run.report, run.run_dir)


@router.get("/runs/{run_id}/stages/{index}/tests", tags=["runs"])
def get_stage_tests(run: Run, index: int) -> StageTestFile:
    """The test file as it was at a stage (0 is the existing tests)."""
    return results.stage_tests(run.report, run.run_dir, index)


@router.get("/runs/{run_id}/stages/{index}/diff", tags=["runs"])
def get_stage_diff(run: Run, index: int) -> StageDiff:
    """How a stage changed the tests it started from."""
    return results.stage_diff(run.report, run.run_dir, index)


@router.get("/runs/{run_id}/stages/{index}/mutants", tags=["runs"])
def list_stage_mutants(
    run: Run,
    index: int,
    status: Annotated[results.MutantFilter, Query(description="Which mutants to list.")] = "undetected",
) -> StageMutants:
    """The mutants of a stage's mutation run, with the source line before and after each change."""
    return results.stage_mutants(run.report, run.run_dir, index, status)


@router.get("/runs/{run_id}/llm", tags=["runs"])
def list_llm_calls(run: Run) -> list[LLMCall]:
    """Every model call of the run, in order, with token usage."""
    return results.llm_calls(run.run_dir)


@router.get("/runs/{run_id}/llm/{name}", tags=["runs"])
def get_llm_call(run: Run, name: str) -> LLMCallDetail:
    """A model call's full prompt and the test file it returned."""
    return results.llm_call(run.run_dir, name)


@router.get("/runs/{run_id}/artifacts", tags=["runs"])
def list_artifacts(run: Run) -> list[Artifact]:
    """Every file in the run directory: report, event log, test versions, prompts, Vitest and Stryker output."""
    return results.artifacts(run.run_dir)


@router.get("/runs/{run_id}/artifacts/{path:path}", tags=["runs"], response_class=FileResponse)
def get_artifact(run: Run, path: str) -> FileResponse:
    """One file from the run directory, as JSON or plain text."""
    file = results.contained(run.run_dir, path)
    if not file.is_file():
        raise HTTPException(404, f"No file {path} in run {run.report.id}.")
    media_type = "application/json" if file.suffix == ".json" else "text/plain; charset=utf-8"
    return FileResponse(file, media_type=media_type)


def _detail(run: RunHandle) -> RunDetail:
    job = run.job
    if job is None:
        return RunDetail(summary=results.summarize(run.report), report=run.report, live=None)
    with job.lock:
        done = job.done.is_set()
        return RunDetail(
            summary=results.summarize(job.report, active=not done),
            report=job.report.model_copy(deep=True),
            live=None if done else job.live(),
        )


def _event(event: RunEvent) -> ServerSentEvent:
    return ServerSentEvent(event="run-event", id=str(event.seq), data=event)
