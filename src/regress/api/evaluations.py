"""Local evaluation results and a single background CLI evaluation job."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel

from regress.evaluation import BugSuite, EvalResult, load_suites
from regress.store import regress_dir


class EvalRequest(BaseModel):
    mode: Literal["oracle", "full"]


class EvalJob(BaseModel):
    status: Literal["idle", "running", "completed", "failed"]
    mode: Literal["oracle", "full"] | None = None
    started_at: datetime | None = None
    exit_code: int | None = None
    output: str = ""


class EvaluationManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._mode: Literal["oracle", "full"] | None = None
        self._started: datetime | None = None
        self._output_path = regress_dir(root) / "evaluation-job.log"
        self._output_file = None

    def suites(self) -> list[BugSuite]:
        return load_suites(self.root) if (self.root / "hidden-bugs").is_dir() else []

    def results(self) -> list[EvalResult]:
        directory = self.root / ".regress" / "eval"
        found = []
        for path in sorted(directory.glob("*/eval.json"), reverse=True):
            try:
                found.append(EvalResult.model_validate_json(path.read_text()))
            except (OSError, ValueError):
                continue
        return found

    def job(self) -> EvalJob:
        with self._lock:
            process = self._process
            if process is None:
                return EvalJob(status="idle")
            code = process.poll()
            try:
                with self._output_path.open("rb") as stream:
                    stream.seek(max(0, self._output_path.stat().st_size - 12000))
                    output = stream.read().decode(errors="replace")
            except OSError:
                output = ""
            if code is not None and self._output_file is not None:
                self._output_file.close()
                self._output_file = None
            return EvalJob(
                status="running" if code is None else "completed" if code == 0 else "failed",
                mode=self._mode,
                started_at=self._started,
                exit_code=code,
                output=output,
            )

    def start(self, mode: Literal["oracle", "full"]) -> EvalJob:
        if not self.suites():
            raise HTTPException(400, "This project has no hidden-bugs/ suites to evaluate.")
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise HTTPException(409, "An evaluation is already running.")
            self._output_file = self._output_path.open("wb")
            command = [sys.executable, "-c", "from regress import main; main()", "eval", str(self.root), "--yes"]
            if mode == "oracle":
                command.append("--oracle")
            try:
                self._process = subprocess.Popen(
                    command,
                    cwd=self.root,
                    stdout=self._output_file,
                    stderr=subprocess.STDOUT,
                    env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
                )
            except OSError as error:
                self._output_file.close()
                self._output_file = None
                raise HTTPException(500, f"Could not start evaluation: {error}") from error
            self._mode = mode
            self._started = datetime.now().astimezone()
        return self.job()

    def shutdown(self) -> None:
        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if self._output_file is not None:
                self._output_file.close()
                self._output_file = None
