from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from regress.errors import ToolTimeout

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.rstrip().splitlines()[-lines:])


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    output: str
    duration_seconds: float


def run_command(args: list[str], cwd: Path, timeout: float, log_path: Path | None = None) -> CommandResult:
    """Run a command with merged stdout/stderr, killing its whole process group on timeout or Ctrl-C.

    Stryker and Vitest spawn worker processes; killing only the parent would leave them running.
    """
    env = {**os.environ, "CI": "1", "FORCE_COLOR": "0", "NO_COLOR": "1"}
    started = time.monotonic()
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        output, _ = proc.communicate()
        output = strip_ansi(output or "")
        _write_log(log_path, args, cwd, f"{output}\n[stopped after {timeout:.0f}s]\n")
        raise ToolTimeout(f"`{' '.join(args[:3])}` timed out after {timeout:.0f}s", output) from None
    except BaseException:
        _kill_group(proc)
        proc.wait()
        raise
    output = strip_ansi(output or "")
    _write_log(log_path, args, cwd, output)
    return CommandResult(args, proc.returncode, output, time.monotonic() - started)


def _write_log(log_path: Path | None, args: list[str], cwd: Path, output: str) -> None:
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(f"$ {' '.join(args)}\n(cwd: {cwd})\n\n{output}")


def _kill_group(proc: subprocess.Popen[str]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
