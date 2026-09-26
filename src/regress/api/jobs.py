"""Runs started over HTTP. Each one runs the pipeline in a process of its own.

A process makes a run cancellable at any point: cancelling sends it SIGINT, the same as Ctrl-C in
`regress run`. The model call, Vitest or Stryker stops at once, the test file is restored, and
the report is saved as cancelled. Progress comes back over a pipe; the server keeps it in memory
for polling and streaming, and in the run's events.jsonl for later.
"""

from __future__ import annotations

import contextlib
import logging
import multiprocessing
import os
import re
import shutil
import signal
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from multiprocessing.connection import Connection
from multiprocessing.context import SpawnContext
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from regress.api.results import EVENTS_FILE, contained, load_events, summarize
from regress.api.schemas import EventType, LiveState, RunEvent, RunRequest, RunSummary
from regress.config import Settings, load_settings
from regress.errors import CandidateRejected, ProjectError, RegressError, ToolError
from regress.llm import LLM, OpenAILLM, require_api_key
from regress.models import RunReport, Stage
from regress.pipeline import Pipeline, RunOptions
from regress.project import Project, find_project_root, load_project
from regress.store import REPORT_FILE, RunStore
from regress.ui import Reporter, format_delta, format_score

log = logging.getLogger("regress.api")

CANCEL_GRACE_SECONDS = 15.0  # then the run is killed, and the server restores the test file itself
STATUS_INTERVAL = 0.25  # at most this often, the model's streaming status is passed on
WATCHDOG_INTERVAL = 2.0
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

LLMFactory = Callable[[str, Settings], LLM]


def openai_llm(model: str, settings: Settings) -> LLM:
    return OpenAILLM(model, settings.reasoning_effort, idle_timeout=settings.llm_timeout)


@dataclass
class RunSpec:
    """Everything the worker process needs. It is pickled to start the process."""

    project: Project
    run_dir: Path
    model: str
    settings: Settings
    options: RunOptions
    llm_factory: LLMFactory


@dataclass
class RunHandle:
    """A run looked up by ID: in progress (with its job) or finished and saved."""

    report: RunReport
    run_dir: Path
    job: Job | None = None


# --- the worker process ----------------------------------------------------------------------


def run_worker(spec: RunSpec, conn: Connection, server_pid: int) -> None:
    signal.signal(signal.SIGINT, _interrupt)
    signal.signal(signal.SIGTERM, _interrupt)
    threading.Thread(target=_watch_server, args=(server_pid,), daemon=True).start()
    reporter = PipeReporter(conn)
    try:
        llm = spec.llm_factory(spec.model, spec.settings)
        Pipeline(spec.project, llm, spec.options, reporter).run(spec.run_dir)
    except KeyboardInterrupt:
        reporter.event("cancelled", "Cancelled. The test file was restored.")
    except RegressError as error:
        output = error.output if isinstance(error, ToolError) else None
        problems = error.problems if isinstance(error, CandidateRejected) else None
        reporter.event("error", str(error), output=output, problems=problems)
    except Exception as error:
        reporter.event("error", f"Unexpected {type(error).__name__}: {error}")
    finally:
        conn.close()


def _interrupt(signum: int, frame: object) -> None:
    # Only the first signal interrupts; a second one must not cut the cleanup short.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise KeyboardInterrupt


def _watch_server(server_pid: int) -> None:
    """Cancel the run if the server goes away, rather than carry on writing to the test file unobserved."""
    while os.getppid() == server_pid:
        time.sleep(WATCHDOG_INTERVAL)
    os.kill(os.getpid(), signal.SIGINT)


class PipeReporter(Reporter):
    """Sends pipeline progress to the server: log events, report snapshots, and live status."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn
        self.report: RunReport | None = None
        self._mutation_runs = 0
        self._last_status = 0.0

    def _send(self, *message: object) -> None:
        with contextlib.suppress(OSError, ValueError):  # the server is gone; the watchdog stops the run
            self.conn.send(message)

    def event(self, kind: EventType, message: str, **data: Any) -> None:
        if self.report is not None:
            self._send("report", self.report.model_dump(mode="json"))
        data = {key: value for key, value in data.items() if value is not None}
        self._send("event", kind, message, data, time.time())

    def start(self, project: Project, report: RunReport) -> None:
        self.report = report
        self.event(
            "start",
            f"Analyzing {report.source_file} with {report.model}",
            source_file=report.source_file,
            test_file=report.test_file,
            test_file_existed=report.test_file_existed,
            toolchain=project.toolchain.describe(),
        )

    @contextmanager
    def activity(self, message: str) -> Iterator[Callable[[str], None]]:
        self.event("activity", message)

        def set_status(text: str) -> None:
            now = time.monotonic()
            if now - self._last_status >= STATUS_INTERVAL:
                self._last_status = now
                self._send("status", text)

        try:
            yield set_status
        finally:
            self._send("idle")

    def baseline(self, stage: Stage, exists: bool) -> None:
        message = f"{stage.test_count} existing tests" if exists else "No existing test file; starting from scratch"
        self.event("baseline", message, tests=stage.test_count, exists=exists)

    def generated(self, baseline: Stage, stage: Stage) -> None:
        added = stage.test_count - baseline.test_count
        self.event("generated", f"Generated {added} tests ({stage.test_count} in total)", **_written(stage, added))

    def rejected(self, attempt: int, attempts: int, problems: list[str]) -> None:
        retry = "; asking the model to fix it" if attempt < attempts else ""
        message = f"Attempt {attempt}/{attempts} rejected{retry}"
        self.event("rejected", message, attempt=attempt, attempts=attempts, problems=problems)

    def mutation(self, stage: Stage) -> None:
        run = stage.mutation
        assert run is not None
        self._mutation_runs += 1
        self.event(
            "mutation",
            f"Mutation run #{self._mutation_runs} ({stage.label.lower()}): {format_score(run.score)}",
            stage=stage.label,
            score=run.score,
            killed=run.killed + run.timeout,
            survived=run.survived,
            no_coverage=run.no_coverage,
            duration_seconds=run.duration_seconds,
        )

    def improving(self, round_number: int, selected: int, undetected: int) -> None:
        sent = f"{selected} of {undetected}" if selected < undetected else str(undetected)
        message = f"Improving tests from {sent} undetected mutants"
        self.event("improving", message, round=round_number, selected=selected, undetected=undetected)

    def improved(self, previous: Stage, stage: Stage) -> None:
        added = stage.test_count - previous.test_count
        details = _written(stage, added) | {"equivalent_mutants": len(stage.equivalent_mutants)}
        self.event("improved", f"Added {added} tests ({stage.test_count} in total)", **details)

    def warn(self, message: str) -> None:
        self.event("warning", message)

    def note(self, message: str) -> None:
        self.event("note", message)

    def finish(self, report: RunReport, run_dir: Path) -> None:
        kept, delta = report.kept, report.improvement
        message = f"Kept {kept.label.lower()} in {report.test_file}" if kept else "Finished without tests to keep"
        if delta is not None and kept is not report.reference:
            message += f" ({format_delta(delta)})"
        score = kept.score if kept else None
        self.event("finished", message, kept_stage=report.kept_stage, score=score, improvement=delta)


def _written(stage: Stage, added: int) -> dict[str, Any]:
    return {
        "added": added,
        "total": stage.test_count,
        "attempts": stage.attempts,
        "llm_seconds": stage.llm_seconds,
        "summary": stage.summary,
    }


# --- the server side -------------------------------------------------------------------------


class Job:
    """A run in progress, as the server sees it."""

    def __init__(self, spec: RunSpec, on_exit: Callable[[Job], None]) -> None:
        project = spec.project
        self.id = spec.run_dir.name
        self.run_dir = spec.run_dir
        self.started_at = datetime.now().astimezone()
        self.report = RunReport(  # until the worker sends its own
            id=self.id,
            created_at=self.started_at,
            project_root=str(project.root),
            source_file=project.source_rel,
            test_file=project.test_rel,
            test_file_existed=project.test_file.is_file(),
            model=spec.model,
        )
        self.events: list[RunEvent] = []
        self.activity: str | None = None
        self.activity_status: str | None = None
        self.activity_started_at: datetime | None = None
        self.cancel_requested = False
        self.version = 0  # bumped on every change, so a stream knows when to send
        self.done = threading.Event()
        self.lock = threading.Lock()
        self._spec = spec
        self._on_exit = on_exit
        self._started = time.monotonic()
        self._error: str | None = None
        self._original_tests = project.test_file.read_text() if project.test_file.is_file() else None

    def start(self, context: SpawnContext) -> None:
        reader, writer = context.Pipe(duplex=False)
        self.process = context.Process(
            target=run_worker, args=(self._spec, writer, os.getpid()), name=f"regress-run-{self.id}"
        )
        self.process.start()
        writer.close()  # so the reader sees EOF once the worker exits
        threading.Thread(target=self._pump, args=(reader,), name=f"regress-pump-{self.id}", daemon=True).start()

    def cancel(self) -> bool:
        with self.lock:
            if self.done.is_set() or self.cancel_requested:
                return False
            self.cancel_requested = True
            self._add_event("cancelling", "Cancelling...")
        self._signal(signal.SIGINT)
        timer = threading.Timer(CANCEL_GRACE_SECONDS, self._signal, args=(signal.SIGKILL,))
        timer.daemon = True
        timer.start()
        return True

    def live(self) -> LiveState:
        return LiveState(
            started_at=self.started_at,
            elapsed_seconds=round(time.monotonic() - self._started, 1),
            activity=self.activity,
            activity_status=self.activity_status,
            activity_started_at=self.activity_started_at,
            cancel_requested=self.cancel_requested,
            last_event=len(self.events),
        )

    def since(self, seq: int) -> tuple[list[RunEvent], LiveState | None, int, bool]:
        """Events after `seq`, the live state, its version, and whether the run is over."""
        with self.lock:
            done = self.done.is_set()
            return self.events[seq:], None if done else self.live(), self.version, done

    def summary(self) -> RunSummary:
        return summarize(self.report, active=not self.done.is_set())

    def _signal(self, signum: int) -> None:
        if not self.done.is_set() and self.process.pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(self.process.pid, signum)

    def _pump(self, conn: Connection) -> None:
        try:
            while True:
                try:
                    message = conn.recv()
                except Exception:  # EOFError once the worker exits, or a message an interrupt cut short
                    break
                try:
                    self._handle(message)
                except Exception:
                    log.exception("Ignoring a malformed message from run %s", self.id)
        finally:
            conn.close()
            self.process.join()
            self._finish()

    def _handle(self, message: tuple[Any, ...]) -> None:
        kind, *payload = message
        with self.lock:
            if kind == "report":
                self.report = RunReport.model_validate(payload[0])
            elif kind == "event":
                event_type, text, data, timestamp = payload
                event = self._add_event(event_type, text, data, datetime.fromtimestamp(timestamp).astimezone())
                if event_type == "activity":
                    self.activity, self.activity_status, self.activity_started_at = text, None, event.time
                elif event_type == "error":
                    self._error = text
            elif kind == "status":
                self.activity_status = payload[0] or None
            elif kind == "idle":
                self.activity = self.activity_status = self.activity_started_at = None
            self.version += 1

    def _add_event(
        self, kind: EventType, message: str, data: dict[str, Any] | None = None, when: datetime | None = None
    ) -> RunEvent:
        event = RunEvent(
            seq=len(self.events) + 1,
            time=when or datetime.now().astimezone(),
            type=kind,
            message=message,
            data=data or {},
        )
        self.events.append(event)
        with (self.run_dir / EVENTS_FILE).open("a") as file:
            file.write(event.model_dump_json() + "\n")
        self.version += 1
        return event

    def _finish(self) -> None:
        with self.lock:
            try:
                self.report = RunReport.model_validate_json((self.run_dir / REPORT_FILE).read_text())
            except (OSError, ValueError):
                self._salvage()
            self.activity = self.activity_status = self.activity_started_at = None
            self.version += 1
        self.done.set()
        self._on_exit(self)

    def _salvage(self) -> None:
        """The worker exited without saving a report (killed, or cancelled while starting): clean up for it."""
        test_file = self._spec.project.test_file
        current = test_file.read_text() if test_file.is_file() else None
        if current != self._original_tests:
            if self._original_tests is None:
                test_file.unlink()
            else:
                test_file.write_text(self._original_tests)
        report = self.report.model_copy(deep=True)
        if self.cancel_requested:
            report.status, report.error = "cancelled", "Interrupted"
            if not any(event.type == "cancelled" for event in self.events):
                self._add_event("cancelled", "Cancelled. The test file was restored.")
        else:
            report.status = "failed"
            report.error = self._error or f"The run stopped unexpectedly (exit code {self.process.exitcode})."
            if self._error is None:
                self._add_event("error", report.error)
        report.duration_seconds = round(time.monotonic() - self._started, 1)
        (self.run_dir / REPORT_FILE).write_text(report.model_dump_json(indent=2))
        self.report = report


class RunManager:
    """Starts runs, one at a time per project, and finds them again by ID."""

    def __init__(self, root: Path, llm_factory: LLMFactory | None = None) -> None:
        self.root = root
        self.store = RunStore(root)
        self.llm_factory = llm_factory or openai_llm
        self.needs_api_key = llm_factory is None
        self._context = multiprocessing.get_context("spawn")
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._busy: str | None = None
        self._summaries: dict[str, tuple[int, RunSummary]] = {}

    def active(self) -> Job | None:
        with self._lock:
            return next(iter(self._jobs.values()), None)

    def start(self, request: RunRequest) -> Job:
        settings = load_settings(self.root, rounds=request.rounds, model=request.model, runner=request.runner)
        source = self.project_path(request.source)
        project = load_project(source, self.project_path(request.test) if request.test else None, settings.runner)
        if project.root != self.root:
            raise ProjectError(
                f"{request.source} belongs to the project in {project.root}. Serve that one instead: "
                f"regress serve {project.root}"
            )
        if not request.generate and not project.test_file.is_file():
            raise ProjectError(f"generate=false improves existing tests, but {project.test_rel} does not exist.")
        if self.needs_api_key:
            require_api_key()
        options = RunOptions(
            rounds=settings.rounds,
            max_repairs=settings.max_repairs,
            max_mutants=settings.max_mutants,
            measure_baseline=request.baseline,
            generate=request.generate,
            vitest_timeout=settings.vitest_timeout,
            stryker_timeout=settings.stryker_timeout,
        )
        with self._lock:
            self._check_idle()
            run_dir = self.store.create(project.source)
            job = Job(RunSpec(project, run_dir, settings.model, settings, options, self.llm_factory), self._exited)
            job.start(self._context)
            self._jobs[job.id] = job
        return job

    def lookup(self, run_id: str) -> RunHandle:
        with self._lock:
            job = self._jobs.get(run_id)
        if job is not None:
            with job.lock:
                return RunHandle(job.report, job.run_dir, job)
        run_dir = self._run_dir(run_id)
        try:
            report = RunReport.model_validate_json((run_dir / REPORT_FILE).read_text())
        except ValueError as error:
            raise HTTPException(500, f"The report of run {run_id} is unreadable: {error}") from error
        return RunHandle(report, run_dir)

    def summaries(self) -> list[RunSummary]:
        """Every run, newest first."""
        found = {s.id: s for s in map(self._saved_summary, self.store.run_dirs()) if s is not None}
        with self._lock:
            jobs = list(self._jobs.values())
        found |= {job.id: job.summary() for job in jobs}
        return sorted(found.values(), key=lambda s: (s.created_at, s.id), reverse=True)

    def events(self, handle: RunHandle, after: int = 0) -> list[RunEvent]:
        if handle.job is not None:
            return handle.job.since(after)[0]
        return load_events(handle.run_dir)[after:]

    def delete(self, run_id: str) -> None:
        with self._lock:
            if run_id in self._jobs:
                raise HTTPException(409, f"Run {run_id} is still in progress. Cancel it first.")
            shutil.rmtree(self._run_dir(run_id))
            self._summaries.pop(run_id, None)

    @contextmanager
    def exclusive(self, reason: str) -> Iterator[None]:
        """Keep runs from starting while something else changes the project, e.g. installing packages."""
        with self._lock:
            self._check_idle()
            self._busy = reason
        try:
            yield
        finally:
            with self._lock:
                self._busy = None

    def shutdown(self) -> None:
        """Cancel what is running and wait for it to clean up."""
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel()
        for job in jobs:
            job.done.wait(CANCEL_GRACE_SECONDS + 5)

    def project_path(self, relative: str) -> Path:
        """A path given relative to the project root. Paths that lead outside the project are refused."""
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ProjectError(f"{relative} is outside the project.")
        return path

    def same_project(self, path: Path) -> bool:
        return find_project_root(path.parent) == self.root

    def _check_idle(self) -> None:
        if self._jobs:
            raise HTTPException(409, f"Run {next(iter(self._jobs))} is still in progress.")
        if self._busy:
            raise HTTPException(409, f"Regress is {self._busy}.")

    def _run_dir(self, run_id: str) -> Path:
        run_dir = contained(self.store.base, run_id) if _RUN_ID.fullmatch(run_id) else None
        if run_dir is None or not (run_dir / REPORT_FILE).is_file():
            raise HTTPException(404, f"No run {run_id}.")
        return run_dir

    def _saved_summary(self, run_dir: Path) -> RunSummary | None:
        path = run_dir / REPORT_FILE
        try:
            stamp = path.stat().st_mtime_ns
            cached = self._summaries.get(run_dir.name)
            if cached is not None and cached[0] == stamp:
                return cached[1]
            summary = summarize(RunReport.model_validate_json(path.read_text()))
        except (OSError, ValueError):
            return None
        self._summaries[run_dir.name] = (stamp, summary)
        return summary

    def _exited(self, job: Job) -> None:
        with self._lock:
            self._jobs.pop(job.id, None)
