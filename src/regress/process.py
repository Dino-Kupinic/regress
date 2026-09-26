from __future__ import annotations

import contextlib
import math
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from regress.errors import ToolError, ToolTimeout
from regress.files import atomic_write_text

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
MAX_OUTPUT_BYTES = 1024 * 1024
_WORKER_RESOURCE_FDS: tuple[int, ...] = ()
# The pipe is held open only by the worker. Even SIGKILL closes it, so a tiny independent
# guardian can reap the tool's entire group when the worker has no chance to run finally.
_GUARDIAN = """\
import os, signal, sys
fd, group = map(int, sys.argv[1:])
try:
    while os.read(fd, 1):
        pass
finally:
    try:
        os.killpg(group, signal.SIGKILL)
    except ProcessLookupError:
        pass
"""


def retain_worker_resources(*descriptors: int) -> None:
    """Keep worker supervision pipes/locks alive in tool guardians until cleanup completes.

    Called only in dedicated API child processes, before any command starts. The guardian
    never writes to these descriptors; holding them delays parent EOF and restart recovery.
    """
    global _WORKER_RESOURCE_FDS
    _WORKER_RESOURCE_FDS = tuple(dict.fromkeys(descriptors))


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


class _Output:
    def __init__(self) -> None:
        self.data = bytearray()
        self.truncated = False

    def append(self, data: bytes) -> None:
        self.data.extend(data)
        if len(self.data) > MAX_OUTPUT_BYTES:
            del self.data[:-MAX_OUTPUT_BYTES]
            self.truncated = True

    def text(self) -> str:
        prefix = "[earlier output truncated]\n" if self.truncated else ""
        return prefix + strip_ansi(self.data.decode("utf-8", errors="replace"))


def run_command(args: list[str], cwd: Path, timeout: float, log_path: Path | None = None) -> CommandResult:
    """Run a bounded command, stopping its process tree on exit, timeout or cancellation.

    Output retains the last MiB, preventing a noisy test from exhausting worker memory.
    An independent guardian also stops tools when the worker is forcibly killed.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Command timeout must be finite and greater than zero.")
    env = {**os.environ, "CI": "1", "FORCE_COLOR": "0", "NO_COLOR": "1"}
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as error:
        raise ToolError(f"Could not start `{args[0]}`: {error}") from error
    output = _Output()
    try:
        with _guardian(proc):
            try:
                _collect(proc, output, timeout)
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                proc.wait()
                _collect(proc, output, 1)
                text = output.text()
                _write_log(log_path, args, cwd, f"{text}\n[stopped after {timeout:.0f}s]\n")
                raise ToolTimeout(f"`{' '.join(args[:3])}` timed out after {timeout:.0f}s", text) from None
            except BaseException:
                _kill_group(proc)
                proc.wait()
                _collect(proc, output, 1)
                _write_log(log_path, args, cwd, output.text() + "\n[interrupted]\n")
                raise
    finally:
        _kill_group(proc)
        proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()
    text = output.text()
    _write_log(log_path, args, cwd, text)
    return CommandResult(args, proc.returncode, text, time.monotonic() - started)


def _collect(proc: subprocess.Popen[bytes], output: _Output, timeout: float) -> None:
    """Drain output without waiting forever on inherited pipes left open by descendants."""
    assert proc.stdout is not None
    deadline = time.monotonic() + timeout
    exited = False
    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ)
        while selector.get_map() or proc.poll() is None:
            if proc.poll() is not None and not exited:
                exited = True
                _kill_group(proc)
                deadline = min(deadline, time.monotonic() + 1)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if exited:
                    break
                raise subprocess.TimeoutExpired(proc.args, timeout)
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fd, 65536)
                if chunk:
                    output.append(chunk)
                else:
                    selector.unregister(key.fileobj)


@contextlib.contextmanager
def _guardian(proc: subprocess.Popen[bytes]) -> Iterator[None]:
    reader, writer = os.pipe()
    guard = None
    try:
        guard = subprocess.Popen(
            [sys.executable, "-c", _GUARDIAN, str(reader), str(proc.pid)],
            pass_fds=(reader, *_WORKER_RESOURCE_FDS),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        os.close(reader)
        reader = -1
        yield
    finally:
        if reader != -1:
            os.close(reader)
        os.close(writer)
        if guard is not None:
            try:
                guard.wait(timeout=2)
            except subprocess.TimeoutExpired:
                guard.kill()
                guard.wait()


def _write_log(log_path: Path | None, args: list[str], cwd: Path, output: str) -> None:
    if log_path is not None:
        atomic_write_text(log_path, f"$ {' '.join(args)}\n(cwd: {cwd})\n\n{output}")


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
