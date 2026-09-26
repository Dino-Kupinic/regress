"""Local evaluation results and a single background CLI evaluation job."""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel

from regress.api.jobs import RunManager, _interrupt, _watch_server
from regress.evaluation import BugSuite, EvalResult, load_suites
from regress.process import retain_worker_resources
from regress.store import regress_dir

log = logging.getLogger("regress.api")
EVALUATION_GRACE_SECONDS = 5.0


def evaluation_worker(server_pid: int, lock_descriptor: int | None = None) -> None:
    """Run the CLI with cooperative cancellation and stop if its API server disappears."""
    from regress import main

    if lock_descriptor is not None:
        retain_worker_resources(lock_descriptor)
    signal.signal(signal.SIGINT, _interrupt)
    signal.signal(signal.SIGTERM, _interrupt)
    threading.Thread(target=_watch_server, args=(server_pid,), daemon=True).start()
    main()


class EvalRequest(BaseModel):
    mode: Literal["oracle", "full"]


class EvalJob(BaseModel):
    status: Literal["idle", "running", "completed", "failed"]
    mode: Literal["oracle", "full"] | None = None
    started_at: datetime | None = None
    exit_code: int | None = None
    output: str = ""


class EvaluationManager:
    def __init__(self, root: Path, *, manager: RunManager | None = None) -> None:
        self.root = root
        self._manager = manager
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._mode: Literal["oracle", "full"] | None = None
        self._started: datetime | None = None
        self._output_path = regress_dir(root) / "evaluation-job.log"
        self._output_file = None
        self._release: Callable[[], None] | None = None
        self._finished = threading.Event()
        self._exit_code: int | None = None
        self._closed = False

    def suites(self) -> list[BugSuite]:
        return load_suites(self.root) if (self.root / "hidden-bugs").is_dir() else []

    def results(self) -> list[EvalResult]:
        directory = self.root / ".regress" / "eval"
        found = []
        if not directory.resolve().is_relative_to(self.root / ".regress"):
            return found
        for path in sorted(directory.glob("*/eval.json"), reverse=True):
            try:
                if not path.resolve().is_relative_to(directory.resolve()):
                    continue
                found.append(EvalResult.model_validate_json(path.read_text()))
            except (OSError, ValueError):
                continue
        return found

    def job(self) -> EvalJob:
        with self._lock:
            self._reap()
            return self._snapshot()

    def _snapshot(self) -> EvalJob:
        if self._process is None:
            return EvalJob(status="idle")
        try:
            descriptor = os.open(self._output_path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                stream.seek(max(0, os.fstat(stream.fileno()).st_size - 12000))
                output = stream.read(12000).decode(errors="replace")
        except OSError:
            output = ""
        code = self._exit_code
        return EvalJob(
            status="running" if code is None else "completed" if code == 0 else "failed",
            mode=self._mode,
            started_at=self._started,
            exit_code=code,
            output=output,
        )

    def _reap(self) -> None:
        if self._process is not None and not self._finished.is_set():
            code = self._process.poll()
            if code is not None:
                self._complete(code)

    def _complete(self, code: int) -> None:
        self._exit_code = code
        self._finished.set()
        try:
            if self._output_file is not None:
                self._output_file.close()
        except OSError:
            log.exception("Could not close the evaluation log")
        finally:
            self._output_file = None
            if self._release is not None:
                self._release()
                self._release = None

    def _monitor(self, finished: threading.Event) -> None:
        while not finished.wait(0.1):
            with self._lock:
                if finished is not self._finished:
                    return
                self._reap()

    def start(self, mode: Literal["oracle", "full"]) -> EvalJob:
        with self._lock:
            if self._closed:
                raise HTTPException(503, "Regress is shutting down.")
            self._reap()
            if self._process is not None and not self._finished.is_set():
                raise HTTPException(409, "An evaluation is already running.")
            release = self._manager.reserve("evaluating") if self._manager is not None else lambda: None
            lock_descriptor = self._manager.lock_descriptor if self._manager is not None else None
            command = [
                sys.executable,
                "-c",
                f"from regress.api.evaluations import evaluation_worker; evaluation_worker({os.getpid()}, {lock_descriptor!r})",
                "eval",
                str(self.root),
                "--yes",
            ]
            if mode == "oracle":
                command.append("--oracle")
            try:
                if not self.suites():
                    raise HTTPException(400, "This project has no hidden-bugs/ suites to evaluate.")
                descriptor = os.open(self._output_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                self._output_file = os.fdopen(descriptor, "wb")
                process = subprocess.Popen(
                    command,
                    cwd=self.root,
                    stdout=self._output_file,
                    stderr=subprocess.STDOUT,
                    env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
                    start_new_session=True,
                    pass_fds=(self._manager.lock_descriptor,) if self._manager is not None else (),
                )
            except BaseException as error:
                if self._output_file is not None:
                    self._output_file.close()
                    self._output_file = None
                release()
                if isinstance(error, OSError):
                    raise HTTPException(500, f"Could not start evaluation: {error}") from error
                raise
            self._process = process
            self._exit_code = None
            self._release = release
            self._finished = threading.Event()
            self._mode = mode
            self._started = datetime.now().astimezone()
            threading.Thread(target=self._monitor, args=(self._finished,), daemon=True).start()
            return self._snapshot()

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            self._reap()
            process = self._process
            if process is None or self._finished.is_set():
                return
        with contextlib.suppress(ProcessLookupError):
            process.send_signal(signal.SIGINT)
        try:
            code = process.wait(timeout=EVALUATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            try:
                code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log.error("Evaluation process %s did not exit before shutdown", process.pid)
                return
        with self._lock:
            self._complete(code)
