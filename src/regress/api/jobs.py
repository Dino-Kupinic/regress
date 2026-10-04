"""Runs started over HTTP. Each one runs the pipeline in a process of its own.

A process makes a run cancellable at any point: cancelling sends it SIGINT, the same as Ctrl-C in
`regress run`. The model call, Vitest or Stryker stops at once, the test file is restored, and
the report is saved as cancelled. Progress comes back over a pipe; the server keeps it in memory
for polling and streaming, and in the run's events.jsonl for later.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import json
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
from multiprocessing import reduction
from multiprocessing.connection import Connection
from multiprocessing.context import SpawnContext
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from regress.api.results import EVENTS_FILE, contained, load_events, summarize
from regress.api.schemas import BatchDetail, BatchRequest, EventType, LiveState, RunEvent, RunRequest, RunSummary
from regress.batch import BatchReport, FileResult, summarize_run
from regress.config import Settings, load_settings
from regress.errors import CandidateRejected, ProjectError, RegressError, ToolError
from regress.estimates import run_options
from regress.files import atomic_write_bytes, atomic_write_text
from regress.llm import LLM, create_llm, require_api_key
from regress.models import RunReport, Stage
from regress.pipeline import Pipeline, RunOptions
from regress.process import retain_worker_resources
from regress.project import Project, find_project_root, load_project
from regress.store import REPORT_FILE, RunStore, regress_dir
from regress.ui import Reporter, format_delta, format_score

log = logging.getLogger("regress.api")

CANCEL_GRACE_SECONDS = 15.0  # then the run is killed, and the server restores the test file itself
LOCK_WAIT_SECONDS = 30.0  # long enough for a previous container's stop timeout to release the project
STATUS_INTERVAL = 0.25  # at most this often, the model's streaming status is passed on
WATCHDOG_INTERVAL = 2.0
RECOVERY_FILE = ".api-recovery.json"
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

LLMFactory = Callable[[str, Settings], LLM]


class _InheritedLock:
    """Pass the server's flock into a spawned worker, so recovery cannot race an orphan."""

    def __init__(self, descriptor: int) -> None:
        self.descriptor = descriptor

    def __reduce__(self):
        return _restore_lock, (reduction.DupFd(self.descriptor),)


def _restore_lock(descriptor: Any) -> _InheritedLock:
    return _InheritedLock(descriptor.detach())


def _durable_unlink(path: Path) -> None:
    """Persist removal before a later run may make the old rollback data obsolete."""
    try:
        path.unlink()
    except FileNotFoundError:
        return
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rollback_path(root: Path, relative: str) -> Path:
    """Keep lexical file names so atomic replacement removes an unexpected final symlink."""
    part = Path(relative)
    path = root / part
    if (
        part.is_absolute()
        or ".." in part.parts
        or "\0" in relative
        or path.suffix not in {".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"}
    ):
        raise ProjectError(f"Cannot restore {relative!r}: invalid path or outside the project.")
    if path.parent.resolve() != path.parent or not path.parent.is_relative_to(root):
        raise ProjectError(f"Cannot restore {relative!r}: its parent directory moved or became a symlink.")
    return path


def provider_llm(model: str, settings: Settings) -> LLM:
    """The configured provider's model: OpenAI, Anthropic, or an OpenAI-compatible server."""
    return create_llm(settings, model)


@dataclass
class RunSpec:
    """Everything the worker process needs. It is pickled to start the process."""

    project: Project
    run_dir: Path
    model: str
    settings: Settings
    options: RunOptions
    llm_factory: LLMFactory
    project_lock: _InheritedLock | None = None
    batch_id: str | None = None


@dataclass
class RunHandle:
    """A run looked up by ID: in progress (with its job) or finished and saved."""

    report: RunReport
    run_dir: Path
    job: Job | None = None


# --- the worker process ----------------------------------------------------------------------


def run_worker(spec: RunSpec, conn: Connection, server_pid: int) -> None:
    retain_worker_resources(conn.fileno(), *([spec.project_lock.descriptor] if spec.project_lock is not None else []))
    signal.signal(signal.SIGINT, _interrupt)
    signal.signal(signal.SIGTERM, _interrupt)
    threading.Thread(target=_watch_server, args=(server_pid,), daemon=True).start()
    reporter = PipeReporter(conn)
    llm = None
    try:
        llm = spec.llm_factory(spec.model, spec.settings)
        Pipeline(spec.project, llm, spec.options, reporter, batch_id=spec.batch_id).run(spec.run_dir)
    except KeyboardInterrupt:
        reporter.event("cancelled", "Cancelled. The test file was restored.")
    except RegressError as error:
        output = error.output if isinstance(error, ToolError) else None
        problems = error.problems if isinstance(error, CandidateRejected) else None
        reporter.event("error", str(error), output=output, problems=problems)
    except Exception as error:
        reporter.event("error", f"Unexpected {type(error).__name__}: {error}")
    finally:
        try:
            close = getattr(llm, "close", None)
            if callable(close):
                close()
        finally:
            conn.close()
            if spec.project_lock is not None:
                os.close(spec.project_lock.descriptor)


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
    time.sleep(CANCEL_GRACE_SECONDS)
    os.kill(os.getpid(), signal.SIGKILL)


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
            batch_id=spec.batch_id,
        )
        self.events: list[RunEvent] = []
        self.activity: str | None = None
        self.activity_status: str | None = None
        self.activity_started_at: datetime | None = None
        self.cancel_requested = False
        self.cleanup_failed = False
        self.version = 0  # bumped on every change, so a stream knows when to send
        self.done = threading.Event()
        self.lock = threading.Lock()
        self._spec = spec
        self._on_exit = on_exit
        self._started = time.monotonic()
        self._error: str | None = None
        self._original_tests = project.test_file.read_bytes() if project.test_file.is_file() else None
        self._original_source = project.source.read_bytes()
        self._cancel_timer: threading.Timer | None = None

    def prepare(self) -> None:
        """Persist rollback data before a worker is allowed to modify project files."""
        journal = {
            "version": 1,
            "report": self.report.model_dump(mode="json"),
            "source": base64.b64encode(self._original_source).decode("ascii"),
            "tests": base64.b64encode(self._original_tests).decode("ascii")
            if self._original_tests is not None
            else None,
        }
        atomic_write_text(self.run_dir / RECOVERY_FILE, json.dumps(journal))
        atomic_write_text(self.run_dir / REPORT_FILE, self.report.model_dump_json(indent=2))

    def start(self, context: SpawnContext) -> None:
        reader, writer = context.Pipe(duplex=False)
        self.process = context.Process(
            target=run_worker, args=(self._spec, writer, os.getpid()), name=f"regress-run-{self.id}"
        )
        try:
            self.process.start()
            writer.close()  # so the reader sees EOF once the worker exits
            threading.Thread(target=self._pump, args=(reader,), name=f"regress-pump-{self.id}", daemon=True).start()
        except BaseException:
            reader.close()
            writer.close()
            if self.process.pid is not None:
                self.process.kill()
                self.process.join(timeout=5)
            raise

    def cancel(self) -> bool:
        with self.lock:
            if self.done.is_set() or self.cancel_requested:
                return False
            self.cancel_requested = True
            self._add_event("cancelling", "Cancelling...")
        self._signal(signal.SIGINT)
        self._cancel_timer = threading.Timer(CANCEL_GRACE_SECONDS, self._signal, args=(signal.SIGKILL,))
        self._cancel_timer.daemon = True
        self._cancel_timer.start()
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
        with self.lock:
            return summarize(self.report, active=not self.done.is_set())

    def _signal(self, signum: int) -> None:
        if not self.done.is_set() and self.process.pid is not None and self.process.is_alive():
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
            self.process.join(timeout=CANCEL_GRACE_SECONDS)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=5)
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
        try:
            with (self.run_dir / EVENTS_FILE).open("a") as file:
                file.write(event.model_dump_json() + "\n")
        except OSError:
            log.exception("Could not persist progress for run %s", self.id)
        self.version += 1
        return event

    def _finish(self) -> None:
        try:
            with self.lock:
                try:
                    saved = RunReport.model_validate_json((self.run_dir / REPORT_FILE).read_text())
                    if saved.status == "running" or (saved.status == "completed" and self._error is not None):
                        self._salvage()
                    else:
                        self.report = saved
                        if saved.status != "completed":
                            self._restore_originals()
                except (OSError, ValueError):
                    self._salvage()
                _durable_unlink(self.run_dir / RECOVERY_FILE)
        except Exception as error:
            self.cleanup_failed = True
            log.exception("Could not finish cleanup for run %s", self.id)
            with self.lock:
                self.report.status = "failed"
                self.report.error = f"Run cleanup failed: {error}"
                self._add_event("error", self.report.error)
        finally:
            with self.lock:
                self.activity = self.activity_status = self.activity_started_at = None
                self.version += 1
            if self._cancel_timer is not None:
                self._cancel_timer.cancel()
            self._on_exit(self)
            self.done.set()

    def _salvage(self) -> None:
        """The worker exited without saving a report (killed, or cancelled while starting): clean up for it."""
        self._restore_originals()
        report = self.report.model_copy(deep=True)
        if self.cancel_requested:
            report.status, report.error = "cancelled", "Interrupted"
            if not any(event.type == "cancelled" for event in self.events):
                self._add_event("cancelled", "Cancelled. The test file was restored.")
        else:
            report.status = "failed"
            process = getattr(self, "process", None)
            report.error = (
                self._error or f"The run stopped unexpectedly (exit code {getattr(process, 'exitcode', None)})."
            )
            if self._error is None:
                self._add_event("error", report.error)
        report.kept_stage = None
        report.duration_seconds = round(time.monotonic() - self._started, 1)
        self.report = report
        atomic_write_text(self.run_dir / REPORT_FILE, report.model_dump_json(indent=2))

    def _restore_originals(self) -> None:
        project = self._spec.project
        source = _rollback_path(project.root, project.source_rel)
        test_file = _rollback_path(project.root, project.test_rel)
        current = test_file.read_bytes() if not test_file.is_symlink() and test_file.is_file() else None
        if test_file.is_symlink() or current != self._original_tests:
            if self._original_tests is None:
                _durable_unlink(test_file)
            else:
                atomic_write_bytes(test_file, self._original_tests)
        if source.is_symlink() or not source.is_file() or source.read_bytes() != self._original_source:
            atomic_write_bytes(source, self._original_source)


class Batch:
    """Several files run one after another, as ordinary runs under one project reservation.

    Each file gets its own run (worker process, recovery record, live view, cancellation); the batch only
    decides what runs next, keeps the combined summary in .regress/batches/<id>.json, and stops early when
    cancelled or when a run's cleanup failed.
    """

    def __init__(
        self,
        manager: RunManager,
        report: BatchReport,
        projects: list[Project | None],
        settings: Settings,
        options: RunOptions,
        release: Callable[[], None],
    ) -> None:
        self.id = report.id
        self.report = report
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.cancel_requested = False
        self.current: Job | None = None
        self._manager = manager
        self._projects = projects  # None: a file that is skipped (no tests to improve)
        self._settings = settings
        self._options = options
        self._release = release
        self._started = time.monotonic()

    def start(self) -> None:
        self._save()
        threading.Thread(target=self._run, name=f"regress-batch-{self.id}", daemon=True).start()

    def cancel(self) -> bool:
        with self.lock:
            if self.done.is_set() or self.cancel_requested:
                return False
            self.cancel_requested = True
            current = self.current
        if current is not None:
            current.cancel()
        return True

    def detail(self) -> BatchDetail:
        with self.lock:
            current = self.current.id if self.current is not None and not self.current.done.is_set() else None
            return _batch_detail(self.report, active=not self.done.is_set(), current_run=current)

    def _run(self) -> None:
        try:
            for project, result in zip(self._projects, self.report.files, strict=True):
                if self._stopping():
                    break
                if project is None:
                    continue  # already marked skipped
                try:
                    job = self._manager._launch(project, self._settings, self._options, _no_release, self.id)
                except Exception as error:
                    with self.lock:
                        result.status, result.error = "failed", f"Could not start the run: {error}"
                    self._save()
                    continue
                with self.lock:
                    self.current = job
                    result.status, result.run_id = "running", job.id
                if self.cancel_requested:  # cancelled while this run was starting
                    job.cancel()
                self._save()
                job.done.wait()
                with job.lock:
                    run = job.report.model_copy(deep=True)
                with self.lock:
                    self.current = None
                    if run.status == "completed":
                        summarize_run(run, result)
                    elif run.status == "cancelled":
                        # Cancelling one of the batch's runs stops the batch, like Ctrl-C in `regress run a b`.
                        self.cancel_requested = True
                        result.status, result.error = "cancelled", run.error
                    else:
                        result.status, result.error = (
                            ("cancelled" if run.status == "cancelled" else "failed"),
                            run.error,
                        )
                    result.cost_usd = run.cost_usd  # a failed or cancelled run still spent something
                self._save()
        except Exception:
            log.exception("Batch %s stopped unexpectedly", self.id)
        finally:
            with self.lock:
                stopped = self.cancel_requested or self._manager.closed
                for result in self.report.files:
                    if result.status in ("running", "pending") and stopped:
                        result.status = "cancelled" if result.status == "running" else "pending"
                if stopped:
                    self.report.status = "cancelled"
                elif self.report.failed or any(f.status == "pending" for f in self.report.files):
                    self.report.status = "failed"
                else:
                    self.report.status = "completed"
                self.report.duration_seconds = round(time.monotonic() - self._started, 1)
            try:
                self._save()
            finally:
                self._release()
                self.done.set()

    def _stopping(self) -> bool:
        return self.cancel_requested or self._manager.closed or self._manager.blocking_problem is not None

    def _save(self) -> None:
        with self.lock:
            report = self.report.model_copy(deep=True)
        try:
            self._manager.store.save_batch(report)
        except Exception:
            log.exception("Could not save batch %s", self.id)


def _no_release() -> None:
    """A batch's runs leave the project reserved; the batch releases it when it is over."""


def _batch_detail(report: BatchReport, active: bool, current_run: str | None = None) -> BatchDetail:
    return BatchDetail(**report.model_dump(), combined=report.totals, active=active, current_run=current_run)


class RunManager:
    """Starts runs, one at a time per project, and finds them again by ID."""

    def __init__(self, root: Path, llm_factory: LLMFactory | None = None) -> None:
        self.root = root
        self.store = RunStore(root)
        self.llm_factory = llm_factory or provider_llm
        self.needs_api_key = llm_factory is None
        self._context = multiprocessing.get_context("spawn")
        self._lock = threading.Lock()
        self._claim_condition = threading.Condition(self._lock)
        self._claiming = False
        self._jobs: dict[str, Job] = {}
        self._batches: dict[str, Batch] = {}
        self._busy: str | None = None
        self._reservation: object | None = None
        self._closed = False
        self.replacing = False  # answering health checks while the previous server still holds the project
        self._problem: str | None = None
        self._project_lock: int | None = None
        self._summaries: dict[str, tuple[int, RunSummary]] = {}

    def startup(self, *, lock_timeout: float = LOCK_WAIT_SECONDS) -> None:
        """Claim this project for one API process, including across Uvicorn workers.

        A replacement process waits until `lock_timeout` for the previous server to exit. Rolling
        deploys start the new container while the old one is still shutting down; failing the lock
        immediately kills that container before the platform can finish the handoff.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("The run manager has already shut down.")
            self._claim_project(lock_timeout)
            self.replacing = False

    def _claim_project(self, lock_timeout: float = LOCK_WAIT_SECONDS) -> None:
        # Waiting for another server releases _lock. Keep one claimant per manager so a
        # concurrent request cannot open a second descriptor and compete with our own flock.
        while self._claiming:
            self._claim_condition.wait()
        if self._closed:
            raise RuntimeError("The run manager has already shut down.")
        if self._project_lock is not None:
            return
        self._claiming = True
        try:
            self._acquire_project(lock_timeout)
        finally:
            self._claiming = False
            self._claim_condition.notify_all()

    def _acquire_project(self, lock_timeout: float) -> None:
        path = regress_dir(self.root) / "api.lock"
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            self._lock_project(descriptor, lock_timeout)
        except BaseException:
            os.close(descriptor)
            raise
        self._project_lock = descriptor
        try:
            self._recover()
        except BaseException:
            os.close(descriptor)
            self._project_lock = None
            raise

    def _lock_project(self, descriptor: int, lock_timeout: float) -> None:
        deadline = time.monotonic() + lock_timeout
        announced = False
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError as error:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        f"Another Regress API server already owns {self.root}. "
                        "Run one server with one worker per project."
                    ) from error
                if not announced:
                    log.warning(
                        "Waiting up to %.0fs for the other API server to release %s",
                        lock_timeout,
                        self.root,
                    )
                    announced = True
                # Let shutdown run while the previous server is still exiting.
                self._lock.release()
                try:
                    time.sleep(min(0.2, remaining))
                finally:
                    self._lock.acquire()
                if self._closed:
                    break
        if self._closed:
            raise RuntimeError("The run manager has already shut down.")

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def lock_descriptor(self) -> int:
        """Descriptor for background children to hold until they exit."""
        assert self._project_lock is not None
        return self._project_lock

    @property
    def blocking_problem(self) -> str | None:
        with self._lock:
            return self._problem

    def _recover(self) -> None:
        """Restore unfinished API-owned runs after every process from the old server has exited."""
        self._recover_runs()
        self._recover_batches()

    def _recover_batches(self) -> None:
        """A batch still marked running belonged to a server that stopped; its runs are recovered on their own."""
        directory = self.store.batch_path("x").parent
        if directory.is_symlink() or not directory.is_dir():
            return
        for path in sorted(directory.glob("*.json")):
            if path.is_symlink():
                continue
            try:
                report = BatchReport.model_validate_json(path.read_text())
            except (OSError, ValueError):
                continue
            if report.status != "running":
                continue
            for result in report.files:
                if result.status == "running":
                    result.status = "failed"
                    result.error = "The API server stopped before this run finished."
            report.status = "cancelled"
            self.store.save_batch(report)
            log.warning("Marked interrupted batch %s as cancelled", report.id)

    def _recover_runs(self) -> None:
        if self.store.base.is_symlink() or not self.store.base.resolve().is_relative_to(self.root):
            raise ProjectError("The run directory must stay inside the project and must not be a symlink.")
        if not self.store.base.is_dir():
            return
        for run_dir in sorted(self.store.base.iterdir()):
            if run_dir.is_symlink() or not run_dir.is_dir():
                continue
            journal_path = run_dir / RECOVERY_FILE
            if journal_path.is_symlink():
                raise ProjectError(f"Cannot recover run {run_dir.name}: its recovery record is a symlink.")
            if not journal_path.is_file():
                continue
            report_path = run_dir / REPORT_FILE
            if report_path.is_symlink():
                raise ProjectError(f"Cannot recover run {run_dir.name}: its report is a symlink.")
            try:
                saved = RunReport.model_validate_json(report_path.read_text())
            except (OSError, ValueError):
                saved = None
            if (
                saved is not None
                and saved.status == "completed"
                and saved.id == run_dir.name
                and Path(saved.project_root).resolve() == self.root
            ):
                _durable_unlink(journal_path)
                continue
            try:
                journal = json.loads(journal_path.read_text())
                if journal["version"] != 1:
                    raise ValueError("unsupported recovery format")
                report = RunReport.model_validate(journal["report"])
                if report.id != run_dir.name or Path(report.project_root).resolve() != self.root:
                    raise ValueError("the recovery record belongs to another run or project")
                source = _rollback_path(self.root, report.source_file)
                test_file = _rollback_path(self.root, report.test_file)
                if source == test_file:
                    raise ValueError("source and test paths are identical")
                original_source = base64.b64decode(journal["source"], validate=True)
                original_tests = None if journal["tests"] is None else base64.b64decode(journal["tests"], validate=True)
            except (KeyError, OSError, TypeError, ValueError) as error:
                raise ProjectError(f"Cannot safely recover run {run_dir.name}: {error}") from error
            atomic_write_bytes(source, original_source)
            if original_tests is None:
                _durable_unlink(test_file)
            else:
                atomic_write_bytes(test_file, original_tests)
            if saved is not None and saved.id == report.id and saved.status in {"failed", "cancelled"}:
                report = saved
            else:
                report.status = "failed"
                report.error = (
                    "The API server stopped before this run finished. Original source and tests were restored."
                )
                report.duration_seconds = max(
                    0.0, (datetime.now().astimezone() - report.created_at.astimezone()).total_seconds()
                )
            report.kept_stage = None
            atomic_write_text(report_path, report.model_dump_json(indent=2))
            _durable_unlink(journal_path)
            log.warning("Recovered interrupted API run %s", run_dir.name)

    def active(self) -> Job | None:
        with self._lock:
            return next((job for job in self._jobs.values() if not job.done.is_set()), None)

    def start(self, request: RunRequest) -> Job:
        release = self.reserve("starting a run")
        try:
            return self._start(request, release)
        except BaseException:
            release()
            raise

    def _start(self, request: RunRequest, release: Callable[[], None]) -> Job:
        settings, options = self._run_settings(request)
        project = self._project(request.source, request.test, settings)
        if not request.generate and not project.test_file.is_file():
            raise ProjectError(f"generate=false improves existing tests, but {project.test_rel} does not exist.")
        return self._launch(project, settings, options, release)

    def _run_settings(self, request: RunRequest | BatchRequest) -> tuple[Settings, RunOptions]:
        settings = load_settings(
            self.root, rounds=request.rounds, model=request.model, runner=request.runner, max_cost=request.max_cost
        )
        if settings.model is None:
            raise ProjectError(
                f"No model chosen for provider {settings.provider}: pass model, or set one in regress.toml."
            )
        if self.needs_api_key:
            require_api_key(settings.provider, settings.api_key_env)
        options = run_options(settings, settings.model, measure_baseline=request.baseline, generate=request.generate)
        return settings, options

    def _project(self, relative: str, test: str | None, settings: Settings) -> Project:
        source = self.project_path(relative)
        project = load_project(source, self.project_path(test) if test else None, settings.runner)
        if project.root != self.root:
            raise ProjectError(
                f"{relative} belongs to the project in {project.root}. Serve that one instead: "
                f"regress serve {project.root}"
            )
        return project

    def _launch(
        self,
        project: Project,
        settings: Settings,
        options: RunOptions,
        release: Callable[[], None],
        batch_id: str | None = None,
    ) -> Job:
        """Start one run's worker. `release` frees the project reservation when the run is over."""
        assert settings.model is not None
        with self._lock:
            if self._closed:
                raise HTTPException(503, "Regress is shutting down.")
            run_dir = self.store.create(project.source)
            job = Job(
                RunSpec(
                    project,
                    run_dir,
                    settings.model,
                    settings,
                    options,
                    self.llm_factory,
                    _InheritedLock(self.lock_descriptor),
                    batch_id,
                ),
                lambda finished: self._exited(finished, release),
            )
            self._jobs[job.id] = job
            try:
                job.prepare()
                job.start(self._context)
            except BaseException as error:
                self._jobs.pop(job.id, None)
                job._error = f"Could not start the run: {error}"
                try:
                    job._salvage()
                    _durable_unlink(run_dir / RECOVERY_FILE)
                except Exception:
                    self._problem = "Workspace cleanup failed. Restart the server to recover the interrupted run."
                    log.exception("Could not finalize a failed startup for run %s", job.id)
                raise
        return job

    def start_batch(self, request: BatchRequest) -> Batch:
        """Run several files one after another. The project stays reserved until the last one is over."""
        sources = list(dict.fromkeys(request.sources))
        release = self.reserve(f"running {len(sources)} files")
        try:
            settings, options = self._run_settings(request)
            projects: list[Project | None] = []
            results: list[FileResult] = []
            problems: list[str] = []
            for relative in sources:
                try:
                    project = self._project(relative, None, settings)
                except ProjectError as error:
                    problems.append(f"{relative}: {error}")
                    continue
                result = FileResult(source_file=project.source_rel)
                if not request.generate and not project.test_file.is_file():
                    result.status, result.error = "skipped", f"No test file to improve ({project.test_rel})."
                    project = None
                projects.append(project)
                results.append(result)
            if problems:
                raise ProjectError("Some files can't be run:\n" + "\n".join(f"  {p}" for p in problems))
            if all(p is None for p in projects):
                raise ProjectError("generate=false improves existing tests, but none of these files has a test file.")
            report = BatchReport(
                id=self.store.create_batch_id(),
                created_at=datetime.now().astimezone(),
                project_root=str(self.root),
                model=settings.model or "",
                files=results,
            )
            batch = Batch(self, report, projects, settings, options, release)
            with self._lock:
                if self._closed:
                    raise HTTPException(503, "Regress is shutting down.")
                self._batches[batch.id] = batch
            batch.start()
            return batch
        except BaseException:
            release()
            raise

    def batch(self, batch_id: str) -> BatchDetail:
        with self._lock:
            batch = self._batches.get(batch_id)
        if batch is not None:
            return batch.detail()
        if not _RUN_ID.fullmatch(batch_id):
            raise HTTPException(404, f"No batch {batch_id}.")
        path = self.store.batch_path(batch_id)
        if path.is_symlink() or not path.is_file():
            raise HTTPException(404, f"No batch {batch_id}.")
        try:
            report = BatchReport.model_validate_json(path.read_text())
        except (OSError, ValueError) as error:
            raise HTTPException(500, f"The summary of batch {batch_id} is unreadable: {error}") from error
        return _batch_detail(report, active=False)

    def cancel_batch(self, batch_id: str) -> BatchDetail:
        with self._lock:
            batch = self._batches.get(batch_id)
        if batch is None or batch.done.is_set() or not batch.cancel():
            raise HTTPException(409, f"Batch {batch_id} is not in progress.")
        return batch.detail()

    def batches(self) -> list[BatchDetail]:
        directory = self.store.batch_path("x").parent
        found: dict[str, BatchDetail] = {}
        if directory.is_dir() and not directory.is_symlink():
            for path in directory.glob("*.json"):
                if path.is_symlink():
                    continue
                try:
                    found[path.stem] = _batch_detail(BatchReport.model_validate_json(path.read_text()), active=False)
                except (OSError, ValueError):
                    continue
        with self._lock:
            batches = list(self._batches.values())
        found |= {batch.id: batch.detail() for batch in batches}
        return sorted(found.values(), key=lambda b: (b.created_at, b.id), reverse=True)

    def active_batch(self) -> Batch | None:
        with self._lock:
            return next((b for b in self._batches.values() if not b.done.is_set()), None)

    def lookup(self, run_id: str) -> RunHandle:
        with self._lock:
            job = self._jobs.get(run_id)
        if job is not None:
            with job.lock:
                return RunHandle(job.report, job.run_dir, job)
        run_dir = self._run_dir(run_id)
        try:
            report = RunReport.model_validate_json((run_dir / REPORT_FILE).read_text())
        except OSError as error:
            raise HTTPException(404, f"No report for run {run_id}.") from error
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
        return [event for event in load_events(handle.run_dir) if event.seq > after]

    def delete(self, run_id: str) -> None:
        with self._lock:
            if run_id in self._jobs:
                raise HTTPException(409, f"Run {run_id} is still in progress. Cancel it first.")
            shutil.rmtree(self._run_dir(run_id))
            self._summaries.pop(run_id, None)

    @contextmanager
    def exclusive(self, reason: str) -> Iterator[None]:
        """Keep runs from starting while something else changes the project, e.g. installing packages."""
        release = self.reserve(reason)
        try:
            yield
        finally:
            release()

    def reserve(self, reason: str) -> Callable[[], None]:
        """Reserve the project for a run, evaluation or install; return an idempotent release callback."""
        token = object()
        with self._lock:
            self._check_idle()
            self._claim_project()
            self._busy = reason
            self._reservation = token

        def release() -> None:
            with self._lock:
                if self._reservation is token:
                    self._busy = None
                    self._reservation = None

        return release

    def shutdown(self) -> None:
        """Cancel what is running and wait for it to clean up."""
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
            batches = list(self._batches.values())
        for batch in batches:
            batch.cancel()
        for job in jobs:
            job.cancel()
        for job in jobs:
            if not job.done.wait(CANCEL_GRACE_SECONDS + 5):
                job._signal(signal.SIGKILL)
                if not job.done.wait(5):
                    log.error("Run %s did not finish cleanup before shutdown", job.id)
        for batch in batches:
            if not batch.done.wait(5):
                log.error("Batch %s did not finish before shutdown", batch.id)
        with self._lock:
            if self._project_lock is not None:
                os.close(self._project_lock)
                self._project_lock = None

    def project_path(self, relative: str) -> Path:
        """A path given relative to the project root. Paths that lead outside the project are refused."""
        try:
            if "\0" in relative:
                raise ValueError("embedded null character")
            path = (self.root / relative).resolve()
        except (OSError, RuntimeError, ValueError) as error:
            raise ProjectError(f"Invalid project path: {relative!r}.") from error
        if not path.is_relative_to(self.root):
            raise ProjectError(f"{relative} is outside the project.")
        return path

    def same_project(self, path: Path) -> bool:
        return find_project_root(path.parent) == self.root

    def _check_idle(self) -> None:
        if self._closed:
            raise HTTPException(503, "Regress is shutting down.")
        if self.replacing or self._claiming:
            raise HTTPException(
                503, "Regress is waiting for the previous server to release the project. Try again shortly."
            )
        if self._problem is not None:
            raise HTTPException(503, self._problem)
        if self._jobs:
            raise HTTPException(409, f"Run {next(iter(self._jobs))} is still in progress.")
        if self._busy:
            raise HTTPException(409, f"Regress is {self._busy}.")

    def _run_dir(self, run_id: str) -> Path:
        run_dir = contained(self.store.base, run_id) if _RUN_ID.fullmatch(run_id) else None
        if run_dir is None or (self.store.base / run_id).is_symlink():
            raise HTTPException(404, f"No run {run_id}.")
        report = contained(run_dir, REPORT_FILE)
        if (run_dir / REPORT_FILE).is_symlink() or not report.is_file():
            raise HTTPException(404, f"No run {run_id}.")
        return run_dir

    def _saved_summary(self, run_dir: Path) -> RunSummary | None:
        if run_dir.is_symlink():
            return None
        if (run_dir / REPORT_FILE).is_symlink():
            return None
        try:
            path = contained(run_dir, REPORT_FILE)
        except HTTPException:
            return None
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

    def _exited(self, job: Job, release: Callable[[], None]) -> None:
        with self._lock:
            if job.cleanup_failed:
                self._problem = "Workspace cleanup failed. Restart the server to recover the interrupted run."
            else:
                self._jobs.pop(job.id, None)
        release()
