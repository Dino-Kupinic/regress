import contextlib
import sys
import time
from pathlib import Path

import pytest

from regress.errors import ToolTimeout
from regress.process import run_command


def test_timeout_stops_the_command_and_keeps_its_output(tmp_path: Path):
    log = tmp_path / "logs/hang.log"
    started = time.monotonic()
    with pytest.raises(ToolTimeout) as error:
        run_command(
            [sys.executable, "-c", "print('started', flush=True); import time; time.sleep(60)"], tmp_path, 1, log
        )

    assert time.monotonic() - started < 10
    assert "timed out after 1s" in str(error.value)
    assert "started" in error.value.output
    # The log is written for a stopped command too, so a hang can be looked into afterwards.
    assert "started" in log.read_text() and "[stopped after 1s]" in log.read_text()


def test_invalid_utf8_is_replaced_instead_of_crashing(tmp_path):
    result = run_command([sys.executable, "-c", "import os; os.write(1, b'hello\\xffworld')"], tmp_path, 5)
    assert result.returncode == 0
    assert result.output == "hello\ufffdworld"


def test_noisy_process_has_bounded_output_and_log(tmp_path):
    from regress.process import MAX_OUTPUT_BYTES

    log = tmp_path / "noisy.log"
    result = run_command(
        [sys.executable, "-c", f"import os; os.write(1, b'x' * {MAX_OUTPUT_BYTES * 3}); print('end')"],
        tmp_path,
        5,
        log,
    )
    assert result.output.startswith("[earlier output truncated]")
    assert result.output.endswith("end\n")
    assert len(result.output) < MAX_OUTPUT_BYTES + 100
    assert log.stat().st_size < MAX_OUTPUT_BYTES + 1000


def test_missing_command_is_a_tool_error(tmp_path):
    from regress.errors import ToolError

    with pytest.raises(ToolError, match="Could not start"):
        run_command([str(tmp_path / "missing-command")], tmp_path, 5)


def test_timeout_kills_spawned_descendants(tmp_path):
    import fcntl
    import os
    import signal

    pid_file = tmp_path / "child.pid"
    lock_file = tmp_path / "child.lock"
    child = (
        f"import fcntl, os, pathlib, time; lock = open({str(lock_file)!r}, 'w'); "
        "fcntl.flock(lock, fcntl.LOCK_EX); "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(60)"
    with pytest.raises(ToolTimeout):
        run_command([sys.executable, "-c", parent], tmp_path, 1)
    pid = int(pid_file.read_text())
    try:
        with lock_file.open() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def test_guardian_stops_tools_after_worker_is_killed(tmp_path):
    import fcntl
    import os
    import signal
    import subprocess

    pid_file = tmp_path / "tool.pid"
    lock_file = tmp_path / "tool.lock"
    tool = (
        f"import fcntl, os, pathlib, time; lock = open({str(lock_file)!r}, 'w'); "
        "fcntl.flock(lock, fcntl.LOCK_EX); "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    worker = (
        "from pathlib import Path; from regress.process import run_command; "
        f"run_command([{sys.executable!r}, '-c', {tool!r}], Path({str(tmp_path)!r}), 60)"
    )
    process = subprocess.Popen([sys.executable, "-c", worker])
    pid = None
    try:
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pid_file.exists()
        pid = int(pid_file.read_text())
        process.kill()
        process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with lock_file.open() as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.02)
        else:
            pytest.fail("Tool survived the forced termination of its worker")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)


def test_guardian_holds_supervision_pipe_and_project_lock_until_cleanup(tmp_path):
    import fcntl
    import os
    import select
    import subprocess

    ready = tmp_path / "guardian-ready"
    lock_path = tmp_path / "project.lock"
    reader, writer = os.pipe()
    guardian_prefix = f"from pathlib import Path; Path({str(ready)!r}).touch(); "
    worker = f"""
import fcntl
from pathlib import Path
import regress.process as process
lock = open({str(lock_path)!r}, 'w')
fcntl.flock(lock, fcntl.LOCK_EX)
process.retain_worker_resources({writer}, lock.fileno())
process._GUARDIAN = (
    {guardian_prefix!r}
    + process._GUARDIAN.replace('finally:', 'finally:\\n    import time; time.sleep(0.5)')
)
process.run_command([{sys.executable!r}, '-c', 'import time; time.sleep(60)'], Path({str(tmp_path)!r}), 60)
"""
    process = subprocess.Popen([sys.executable, "-c", worker], pass_fds=(writer,))
    os.close(writer)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        process.kill()
        process.wait(timeout=5)
        # Simulate a deliberately slow guardian: no parent rollback or restart may run yet.
        assert not select.select([reader], [], [], 0.1)[0]
        with lock_path.open() as contender:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert select.select([reader], [], [], 5)[0]
            assert os.read(reader, 1) == b""
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        os.close(reader)
