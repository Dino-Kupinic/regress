"""Failure, concurrency and cancellation guarantees without invoking Node or a model API."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import slow_llm
from fastapi import HTTPException
from fastapi.testclient import TestClient

from regress.api import evaluations as evaluation_api
from regress.api import jobs
from regress.api.app import create_app
from regress.api.evaluations import EvaluationManager
from regress.api.jobs import Job, RunManager, RunSpec
from regress.api.schemas import RunRequest
from regress.config import Settings
from regress.errors import ProjectError
from regress.pipeline import RunOptions
from regress.project import Project, Toolchain


class EvaluationProcess:
    pid = 99999999

    def __init__(self, *, ignore_interrupt: bool = False) -> None:
        self.returncode: int | None = None
        self.ignore_interrupt = ignore_interrupt
        self.signals: list[int] = []
        self.waits: list[float | None] = []

    def poll(self):
        return self.returncode

    def send_signal(self, signum):
        self.signals.append(signum)
        if not self.ignore_interrupt:
            self.returncode = -signum

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired("evaluation", timeout)
        return self.returncode


def prepare_project(root: Path) -> None:
    (root / "src/math.ts").write_text("export const add = (a: number, b: number) => a + b;\n")
    directory = root / "hidden-bugs"
    directory.mkdir()
    (directory / "math.json").write_text(
        json.dumps(
            {
                "source": "src/math.ts",
                "test": "test/math.test.ts",
                "bugs": [],
            }
        )
    )


def test_run_evaluation_and_install_share_one_atomic_reservation(js_project, monkeypatch):
    prepare_project(js_project)
    process = EvaluationProcess()
    monkeypatch.setattr(evaluation_api.subprocess, "Popen", lambda *args, **kwargs: process)
    manager = RunManager(js_project, llm_factory=slow_llm)
    evaluations = EvaluationManager(js_project, manager=manager)
    barrier = threading.Barrier(3)
    release_install = threading.Event()
    outcomes = []
    outcomes_ready = threading.Event()
    outcomes_lock = threading.Lock()

    def attempt(kind):
        barrier.wait(timeout=5)
        try:
            if kind == "run":
                manager.start(RunRequest(source="src/math.ts"))
            elif kind == "evaluation":
                evaluations.start("oracle")
            else:
                with manager.exclusive("installing"):
                    release_install.wait(timeout=5)
            result = "started"
        except HTTPException as error:
            result = error.status_code
        with outcomes_lock:
            outcomes.append(result)
            # A successful installer remains inside its reservation until both rivals have lost.
            if len(outcomes) >= 2:
                outcomes_ready.set()

    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(attempt, kind) for kind in ("run", "evaluation", "install")]
            assert outcomes_ready.wait(timeout=5)
            release_install.set()
            for future in futures:
                future.result(timeout=10)
        assert outcomes.count("started") == 1
        assert outcomes.count(409) == 2
    finally:
        release_install.set()
        evaluations.shutdown()
        manager.shutdown()


def test_finished_evaluation_releases_project_without_client_polling(js_project, monkeypatch):
    prepare_project(js_project)
    process = EvaluationProcess()
    monkeypatch.setattr(evaluation_api.subprocess, "Popen", lambda *args, **kwargs: process)
    manager = RunManager(js_project)
    evaluations = EvaluationManager(js_project, manager=manager)
    try:
        evaluations.start("oracle")
        with pytest.raises(HTTPException, match="evaluating"), manager.exclusive("installing"):
            pass
        process.returncode = 0
        deadline = time.monotonic() + 3
        while True:
            try:
                with manager.exclusive("installing"):
                    break
            except HTTPException:
                assert time.monotonic() < deadline, "evaluation never released its reservation"
                time.sleep(0.02)
        assert evaluations.job().status == "completed"
        assert evaluations._output_file is None
    finally:
        evaluations.shutdown()
        manager.shutdown()


def test_failed_evaluation_spawn_releases_reservation_and_log(js_project, monkeypatch):
    prepare_project(js_project)
    manager = RunManager(js_project)
    evaluations = EvaluationManager(js_project, manager=manager)

    def fail(*args, **kwargs):
        raise OSError("process quota exhausted")

    monkeypatch.setattr(evaluation_api.subprocess, "Popen", fail)
    try:
        with pytest.raises(HTTPException, match="Could not start evaluation"):
            evaluations.start("oracle")
        assert evaluations._output_file is None
        with manager.exclusive("installing"):
            pass
    finally:
        evaluations.shutdown()
        manager.shutdown()


def test_failed_run_spawn_releases_reservation(js_project, monkeypatch):
    prepare_project(js_project)
    manager = RunManager(js_project, llm_factory=slow_llm)

    def fail(*args, **kwargs):
        raise OSError("process quota exhausted")

    monkeypatch.setattr(jobs.Job, "start", fail)
    try:
        with pytest.raises(OSError, match="process quota"):
            manager.start(RunRequest(source="src/math.ts"))
        assert manager.active() is None
        with manager.exclusive("installing"):
            pass
    finally:
        manager.shutdown()


def test_rolling_handoff_answers_ready_while_the_previous_server_holds_the_lock(js_project):
    holder = RunManager(js_project)
    holder.startup(lock_timeout=0)
    try:
        with TestClient(create_app(js_project), base_url="http://127.0.0.1") as client:
            assert client.get("/api/ready").status_code == 200
            assert client.app.state.manager.replacing
            holder.shutdown()
            deadline = time.monotonic() + 5
            while client.app.state.manager.replacing:
                assert time.monotonic() < deadline
                time.sleep(0.05)
            assert client.app.state.manager.lock_descriptor >= 0
    finally:
        holder.shutdown()


def test_startup_waits_for_the_previous_server_to_release_the_project(js_project):
    first = RunManager(js_project)
    second = RunManager(js_project)
    first.startup(lock_timeout=0)

    def release() -> None:
        time.sleep(0.3)
        first.shutdown()

    threading.Thread(target=release).start()
    try:
        second.startup(lock_timeout=2)
    finally:
        first.shutdown()
        second.shutdown()


@pytest.mark.parametrize("initial_claim", ["startup", "reservation"])
def test_pending_project_claim_rejects_mutations_without_competing_for_the_lock(js_project, monkeypatch, initial_claim):
    holder = RunManager(js_project)
    replacement = RunManager(js_project)
    holder.startup(lock_timeout=0)
    replacement.replacing = initial_claim == "startup"
    claiming = threading.Event()
    descriptors = []
    lock_project = replacement._lock_project

    def tracked_claim(descriptor, lock_timeout):
        descriptors.append(descriptor)
        claiming.set()
        lock_project(descriptor, lock_timeout=2)

    monkeypatch.setattr(replacement, "_lock_project", tracked_claim)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(
                replacement.startup if initial_claim == "startup" else lambda: replacement.reserve("installing")
            )
            try:
                assert claiming.wait(timeout=1)
                with pytest.raises(HTTPException, match="waiting for the previous server") as rejected:
                    replacement.start(RunRequest(source="src/math.ts"))
                assert rejected.value.status_code == 503
                with pytest.raises(HTTPException) as rejected, replacement.exclusive("installing"):
                    pass
                assert rejected.value.status_code == 503
                assert len(descriptors) == 1, "a mutation opened a competing project-lock descriptor"
            finally:
                holder.shutdown()
            release = pending.result(timeout=3)
            if release is not None:
                release()
        with replacement.exclusive("installing"):
            pass
        assert len(descriptors) == 1
        assert not replacement.replacing
    finally:
        holder.shutdown()
        replacement.shutdown()


def test_concurrent_startup_callers_share_one_project_claim(js_project, monkeypatch):
    holder = RunManager(js_project)
    replacement = RunManager(js_project)
    holder.startup(lock_timeout=0)
    claiming, waiting = threading.Event(), threading.Event()
    descriptors = []
    lock_project = replacement._lock_project
    wait = replacement._claim_condition.wait

    def tracked_claim(descriptor, lock_timeout):
        descriptors.append(descriptor)
        claiming.set()
        lock_project(descriptor, lock_timeout=2)

    def tracked_wait():
        waiting.set()
        return wait()

    monkeypatch.setattr(replacement, "_lock_project", tracked_claim)
    monkeypatch.setattr(replacement._claim_condition, "wait", tracked_wait)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(replacement.startup)
            try:
                assert claiming.wait(timeout=1)
                second = executor.submit(replacement.startup)
                assert waiting.wait(timeout=1)
            finally:
                holder.shutdown()
            first.result(timeout=3)
            second.result(timeout=3)
        assert len(descriptors) == 1
    finally:
        holder.shutdown()
        replacement.shutdown()


def test_one_api_server_per_project_and_shutdown_rejects_new_work(js_project):
    first = RunManager(js_project)
    second = RunManager(js_project)
    try:
        first.startup()
        with pytest.raises(RuntimeError, match="one worker per project"):
            second.startup(lock_timeout=0)
        first.shutdown()
        second.startup()
        with pytest.raises(HTTPException) as rejected:
            first.reserve("installing")
        assert rejected.value.status_code == 503
    finally:
        first.shutdown()
        second.shutdown()


def test_evaluation_shutdown_interrupts_then_kills_group_with_bounded_waits(js_project, monkeypatch):
    prepare_project(js_project)
    process = EvaluationProcess(ignore_interrupt=True)
    monkeypatch.setattr(evaluation_api.subprocess, "Popen", lambda *args, **kwargs: process)
    killed = []

    def kill_group(pid, signum):
        killed.append((pid, signum))
        process.returncode = -signum

    monkeypatch.setattr(evaluation_api.os, "killpg", kill_group)
    manager = RunManager(js_project)
    evaluations = EvaluationManager(js_project, manager=manager)
    try:
        evaluations.start("oracle")
        evaluations.shutdown()
        assert process.signals == [signal.SIGINT]
        assert killed == [(process.pid, signal.SIGKILL)]
        assert all(timeout is not None and timeout > 0 for timeout in process.waits)
        assert evaluations.job().status == "failed"
        with manager.exclusive("installing"):
            pass
        with pytest.raises(HTTPException) as rejected:
            evaluations.start("oracle")
        assert rejected.value.status_code == 503
    finally:
        evaluations.shutdown()
        manager.shutdown()


def make_job(root: Path, on_exit) -> Job:
    source = root / "src/math.ts"
    test = root / "test/math.test.ts"
    test.parent.mkdir()
    source.write_bytes(b"export const add = (a, b) => a + b;\r\n")
    test.write_bytes(b"// original tests\r\n")
    project = Project(root, source, test, Toolchain("npx", "4", "10", "10"))
    directory = root / ".regress/runs/salvage"
    directory.mkdir(parents=True)
    job = Job(RunSpec(project, directory, "test", Settings(), RunOptions(), slow_llm), on_exit)
    job.process = SimpleNamespace(exitcode=-signal.SIGKILL)
    return job


def test_force_killed_run_restores_both_files_and_replaces_running_report(js_project):
    finished = []
    job = make_job(js_project, finished.append)
    job._spec.project.source.write_text("changed source")
    job._spec.project.test_file.write_text("changed tests")
    (job.run_dir / "report.json").write_text(job.report.model_dump_json())
    job.cancel_requested = True
    job._finish()
    assert job._spec.project.source.read_bytes().endswith(b"a + b;\r\n")
    assert job._spec.project.test_file.read_bytes() == b"// original tests\r\n"
    assert job.report.status == "cancelled"
    assert json.loads((job.run_dir / "report.json").read_text())["status"] == "cancelled"
    assert job.done.is_set() and finished == [job]


def test_report_write_failure_does_not_leave_job_active(js_project, monkeypatch):
    finished = []
    job = make_job(js_project, finished.append)

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(jobs, "atomic_write_text", fail)
    job._finish()
    assert job.done.is_set() and finished == [job]
    assert job.report.status == "failed"
    assert "cleanup failed" in job.report.error


def test_evaluation_log_symlink_cannot_truncate_external_file(js_project, tmp_path):
    prepare_project(js_project)
    external = tmp_path / "important.txt"
    external.write_text("keep me")
    evaluations = EvaluationManager(js_project)
    evaluations._output_path.symlink_to(external)
    with pytest.raises(HTTPException, match="Could not start evaluation"):
        evaluations.start("oracle")
    assert external.read_text() == "keep me"


@pytest.mark.parametrize("report_exists", [True, False])
def test_restart_restores_journaled_files_and_marks_interrupted_run_failed(js_project, report_exists):
    previous = RunManager(js_project)
    previous.startup()
    job = make_job(js_project, lambda _: None)
    job.prepare()
    source, test = job._spec.project.source, job._spec.project.test_file
    source.write_bytes(b"changed source")
    test.write_bytes(b"candidate tests")
    if not report_exists:
        (job.run_dir / "report.json").unlink()
    previous.shutdown()  # Simulate a new process with none of the previous job's in-memory state.
    replacement = RunManager(js_project)
    try:
        replacement.startup()
        report = replacement.lookup(job.id).report
        assert report.status == "failed" and "restored" in report.error
        assert source.read_bytes().endswith(b"a + b;\r\n")
        assert test.read_bytes() == b"// original tests\r\n"
        assert not (job.run_dir / jobs.RECOVERY_FILE).exists()
    finally:
        replacement.shutdown()


def test_restart_preserves_completed_output_and_ignores_runs_without_journal(js_project):
    job = make_job(js_project, lambda _: None)
    job.prepare()
    job.report.status = "completed"
    (job.run_dir / "report.json").write_text(job.report.model_dump_json())
    job._spec.project.test_file.write_bytes(b"completed tests")
    legacy = job.run_dir.parent / "legacy-cli-run"
    legacy.mkdir()
    legacy_report = job.report.model_copy(update={"id": legacy.name, "status": "running"})
    (legacy / "report.json").write_text(legacy_report.model_dump_json())
    manager = RunManager(js_project)
    try:
        manager.startup()
        assert manager.lookup(job.id).report.status == "completed"
        assert job._spec.project.test_file.read_bytes() == b"completed tests"
        assert not (job.run_dir / jobs.RECOVERY_FILE).exists()
        assert manager.lookup(legacy.name).report.status == "running"
    finally:
        manager.shutdown()


def test_restart_removes_new_test_file_from_interrupted_run(js_project):
    job = make_job(js_project, lambda _: None)
    test_file = job._spec.project.test_file
    test_file.unlink()
    job._original_tests = None
    job.report.test_file_existed = False
    job.prepare()
    test_file.write_text("unfinished new tests")
    manager = RunManager(js_project)
    try:
        manager.startup()
        assert not test_file.exists()
        assert manager.lookup(job.id).report.status == "failed"
    finally:
        manager.shutdown()


def test_restart_rejects_recovery_paths_outside_project_before_writing(js_project):
    job = make_job(js_project, lambda _: None)
    job.prepare()
    external = js_project.parent / "outside.ts"
    external.write_text("do not change")
    journal_path = job.run_dir / jobs.RECOVERY_FILE
    journal = json.loads(journal_path.read_text())
    journal["report"]["source_file"] = "../outside.ts"
    journal_path.write_text(json.dumps(journal))
    manager = RunManager(js_project)
    with pytest.raises(ProjectError, match="outside the project"):
        manager.startup()
    assert external.read_text() == "do not change"
    assert journal_path.exists()
    assert manager._project_lock is None


def test_live_worker_keeps_project_lock_if_server_loses_its_descriptor(js_project):
    prepare_project(js_project)
    manager = RunManager(js_project, llm_factory=slow_llm)
    contender = RunManager(js_project)
    try:
        job = manager.start(RunRequest(source="src/math.ts"))
        deadline = time.monotonic() + 10
        while not (job.activity or "").startswith("Generating tests"):
            assert not job.done.is_set()
            assert time.monotonic() < deadline
            time.sleep(0.02)
        os.close(manager.lock_descriptor)
        manager._project_lock = None
        with pytest.raises(RuntimeError, match="one worker per project"):
            contender.startup(lock_timeout=0)
        manager.shutdown()
        assert job.done.is_set()
        contender.startup()
        assert contender.lookup(job.id).report.status == "cancelled"
    finally:
        manager.shutdown()
        contender.shutdown()


def test_report_symlink_and_invalid_project_paths_are_rejected(js_project):
    job = make_job(js_project, lambda _: None)
    external = js_project.parent / "report.json"
    external.write_text(job.report.model_dump_json())
    (job.run_dir / "report.json").symlink_to(external)
    manager = RunManager(js_project)
    with pytest.raises(HTTPException) as rejected:
        manager.lookup(job.id)
    assert rejected.value.status_code == 404
    with pytest.raises(ProjectError, match="Invalid project path"):
        manager.project_path("src/\0math.ts")


def test_worker_error_overrides_completed_report_when_persistence_failed(js_project):
    job = make_job(js_project, lambda _: None)
    job.prepare()
    job._spec.project.test_file.write_text("candidate tests")
    job.report.status = "completed"
    (job.run_dir / "report.json").write_text(job.report.model_dump_json())
    job._error = "Could not sync the report to disk"
    job._finish()
    assert job.report.status == "failed"
    assert job.report.error == job._error
    assert job._spec.project.test_file.read_bytes() == b"// original tests\r\n"
    assert not (job.run_dir / jobs.RECOVERY_FILE).exists()


def test_failed_finalization_is_restored_before_journal_is_removed(js_project):
    job = make_job(js_project, lambda _: None)
    job.prepare()
    job._spec.project.source.write_text("broken source")
    job._spec.project.test_file.write_text("candidate tests")
    job.report.status = "failed"
    job.report.error = "Unable to finalize the workspace"
    (job.run_dir / "report.json").write_text(job.report.model_dump_json())
    job._finish()
    assert job._spec.project.source.read_bytes().endswith(b"a + b;\r\n")
    assert job._spec.project.test_file.read_bytes() == b"// original tests\r\n"
    assert not (job.run_dir / jobs.RECOVERY_FILE).exists()


def test_incomplete_cleanup_blocks_new_work_until_restart_recovery(js_project, monkeypatch):
    manager = RunManager(js_project)
    release = manager.reserve("running")
    job = make_job(js_project, lambda finished: manager._exited(finished, release))
    job.prepare()
    manager._jobs[job.id] = job
    job._spec.project.test_file.write_text("candidate tests")

    def fail(*args, **kwargs):
        raise OSError("disk full")

    try:
        with monkeypatch.context() as context:
            context.setattr(jobs, "atomic_write_text", fail)
            job._finish()
        assert manager.blocking_problem is not None
        assert manager.active() is None
        assert manager.lookup(job.id).report.status == "failed"
        assert (job.run_dir / jobs.RECOVERY_FILE).exists()
        with pytest.raises(HTTPException) as rejected:
            manager.start(RunRequest(source="src/math.ts"))
        assert rejected.value.status_code == 503
        with pytest.raises(HTTPException) as rejected, manager.exclusive("installing"):
            pass
        assert rejected.value.status_code == 503
    finally:
        manager.shutdown()
    replacement = RunManager(js_project)
    try:
        replacement.startup()
        assert replacement.blocking_problem is None
        assert not (job.run_dir / jobs.RECOVERY_FILE).exists()
        assert job._spec.project.test_file.read_bytes() == b"// original tests\r\n"
    finally:
        replacement.shutdown()


@pytest.mark.parametrize("recover", [False, True])
def test_restoration_replaces_final_symlink_without_changing_its_target(js_project, recover):
    job = make_job(js_project, lambda _: None)
    job.prepare()
    source = job._spec.project.source
    other = js_project / "src/other.ts"
    other.write_bytes(b"another source file")
    source.unlink()
    source.symlink_to(other)
    manager = RunManager(js_project)
    try:
        if recover:
            manager.startup()
        else:
            job._finish()
        assert not source.is_symlink()
        assert source.read_bytes().endswith(b"a + b;\r\n")
        assert other.read_bytes() == b"another source file"
    finally:
        manager.shutdown()


def test_recovery_refuses_changed_parent_symlink_without_writing(js_project):
    job = make_job(js_project, lambda _: None)
    job.prepare()
    original_directory = js_project / "src"
    moved_directory = js_project / "moved"
    original_directory.rename(moved_directory)
    original_directory.symlink_to(moved_directory, target_is_directory=True)
    manager = RunManager(js_project)
    with pytest.raises(ProjectError, match="parent directory moved"):
        manager.startup()
    assert original_directory.is_symlink()
    assert (job.run_dir / jobs.RECOVERY_FILE).exists()
