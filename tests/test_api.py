"""The HTTP API, against a fake project: no real Vitest or Stryker needed."""

from __future__ import annotations

import json
import os
import signal
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

import pytest
from conftest import ScriptedFactory, failing_llm, slow_llm
from fastapi.testclient import TestClient

from regress.api import create_app
from regress.models import Mutant, MutantStatus, MutationRun, RunReport, Stage

SOURCE = "export const add = (a: number, b: number) => a + b;\nexport const neg = (a: number) => -a;\n"


NO_MODEL = ScriptedFactory()  # for tests that never reach the model


def client_for(root: Path, llm_factory=NO_MODEL) -> TestClient:
    return TestClient(create_app(root, llm_factory=llm_factory), base_url="http://127.0.0.1")


@pytest.fixture
def project(js_project: Path) -> Path:
    (js_project / "src/math.ts").write_text(SOURCE)
    return js_project


@pytest.fixture
def client(project: Path) -> Iterator[TestClient]:
    with client_for(project) as client:
        yield client


def wait_for(client: TestClient, run_id: str, done: Callable[[dict], bool], timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        detail = client.get(f"/api/runs/{run_id}").json()
        if done(detail):
            return detail
        if time.monotonic() > deadline:
            raise AssertionError(f"Run {run_id} did not get there in {timeout}s: {detail['summary']}")
        time.sleep(0.05)


def finished(detail: dict) -> bool:
    return not detail["summary"]["active"]


# --- project ---------------------------------------------------------------------------------


def test_health_and_openapi_schema(client, project):
    assert client.get("/api/health").json()["project_root"] == str(project)
    schema = client.get("/api/openapi.json").json()
    assert schema["paths"]["/api/runs"]["post"]["operationId"] == "start_run"
    assert "RunDetail" in schema["components"]["schemas"]


def test_project_is_ready_when_tools_and_model_are_available(client):
    info = client.get("/api/project").json()
    assert info["ready"] and info["problems"] == []
    assert info["toolchain"]["vitest"] == "4.1.0"
    assert [p["version"] for p in info["packages"]] == ["4.1.0", "10.0.0", "10.0.0"]
    assert info["active_run"] is None


def test_project_lists_what_blocks_a_run(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "shop"}')
    with client_for(tmp_path, llm_factory=None) as client:  # the real OpenAI model, without a key
        info = client.get("/api/project").json()
    assert info["name"] == "shop"
    assert not info["ready"] and info["toolchain"] is None
    assert "Missing dev dependencies" in info["problems"][0]
    assert "OPENAI_API_KEY" in info["problems"][1]
    assert all(p["version"] is None for p in info["packages"])


def test_sources_leave_out_tests_configs_and_dependencies(client, project):
    for name in ("src/math.test.ts", "test/other.ts", "vitest.config.ts", "src/types.d.ts", ".cache/x.ts"):
        (project / name).parent.mkdir(parents=True, exist_ok=True)
        (project / name).write_text("export {};\n")
    (project / "node_modules/vitest/index.js").write_text("")

    assert [s["path"] for s in client.get("/api/project/sources").json()] == ["src/math.ts"]
    detail = client.get("/api/project/sources/src/math.ts").json()
    assert detail == {
        "path": "src/math.ts",
        "test_file": "src/math.test.ts",
        "test_file_exists": True,
        "import_path": "./math",
        "lines": 2,
    }
    assert client.get("/api/project/sources/src/math.test.ts").status_code == 400


def test_new_test_file_location_is_reported(client, project):
    (project / "test").mkdir()
    detail = client.get("/api/project/sources/src/math.ts").json()
    assert detail["test_file"] == "test/math.test.ts"
    assert not detail["test_file_exists"]


def test_only_project_sources_can_be_read(client, project):
    (project / ".env").write_text("OPENAI_API_KEY=sk-secret\n")
    (project / "node_modules/vitest/index.js").write_text("")
    (project.parent / "outside.ts").write_text("")

    assert client.get("/api/project/files/src/math.ts").json()["content"] == SOURCE
    for path in (".env", "package.json", "node_modules/vitest/index.js", "..%2Foutside.ts"):
        response = client.get(f"/api/project/files/{path}")
        assert response.status_code == 403, path
        assert "sk-secret" not in response.text
    assert client.get("/api/project/files/src/missing.ts").status_code == 404


# --- settings and models ---------------------------------------------------------------------


def test_settings_changes_go_to_the_user_config(client, project):
    before = client.get("/api/settings").json()
    assert before["effective"]["model"] == "gpt-6-luna"
    assert before["model_source"] == "built-in default"

    after = client.patch("/api/settings", json={"model": "gpt-6-sol", "rounds": 2}).json()
    assert after["effective"]["model"] == "gpt-6-sol" and after["effective"]["rounds"] == 2
    assert after["model_source"] == "user config"
    assert after["user_config"] == {"model": "gpt-6-sol", "rounds": 2}

    cleared = client.patch("/api/settings", json={"rounds": None}).json()
    assert cleared["user_config"] == {"model": "gpt-6-sol"}
    assert cleared["effective"]["rounds"] == 1

    (project / "regress.toml").write_text('model = "gpt-5.5"\n')
    pinned = client.get("/api/settings").json()
    assert pinned["effective"]["model"] == "gpt-5.5" and pinned["model_source"] == "regress.toml"


def test_invalid_settings_are_refused(client):
    assert client.patch("/api/settings", json={"rounds": 9}).status_code == 422
    assert client.patch("/api/settings", json={"colour": "blue"}).status_code == 422


def test_models_fall_back_to_the_sdk_list_without_an_api_key(client):
    models = client.get("/api/models").json()
    assert models["source"] == "sdk" and not models["verified"]
    assert "OPENAI_API_KEY" in models["note"]
    assert [m["id"] for m in models["models"] if m["default"]] == ["gpt-6-luna"]


# --- starting runs ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "status", "detail"),
    [
        ({"source": "src/missing.ts"}, 400, "Source file not found"),
        ({"source": "../elsewhere.ts"}, 400, "outside the project"),
        ({"source": "src/math.ts", "generate": False}, 400, "does not exist"),
        ({"source": "src/math.ts", "rounds": 7}, 422, None),
        ({"source": "src/math.ts", "colour": "blue"}, 422, None),
    ],
)
def test_bad_run_requests_are_refused_up_front(client, body, status, detail):
    response = client.post("/api/runs", json=body)
    assert response.status_code == status
    if detail:
        assert detail in response.json()["detail"]
    assert client.get("/api/runs").json() == []


def test_a_run_needs_an_api_key_for_the_openai_model(project):
    with client_for(project, llm_factory=None) as client:
        response = client.post("/api/runs", json={"source": "src/math.ts"})
    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]


def test_other_sites_cannot_change_anything(client):
    evil = client.post("/api/runs", json={"source": "src/math.ts"}, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    assert client.post("/api/runs/x/cancel", headers={"Origin": "null"}).status_code == 403

    # The dev server's origin gets through to validation, and its preflights are answered.
    allowed = client.post("/api/runs", json={"source": "nope.ts"}, headers={"Origin": "http://localhost:5173"})
    assert allowed.status_code == 400
    preflight = client.options(
        "/api/runs",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_only_local_host_names_are_served(project):
    with TestClient(create_app(project), base_url="http://attacker.example") as client:
        assert client.get("/api/health").status_code == 400
    with TestClient(create_app(project), base_url="http://[::1]:8765") as client:
        assert client.get("/api/health").status_code == 200


def test_a_failed_run_keeps_its_error_and_log(project):
    with client_for(project, failing_llm) as client:
        started = client.post("/api/runs", json={"source": "src/math.ts"})
        assert started.status_code == 202
        run_id = started.json()["summary"]["id"]
        assert started.headers["location"] == f"/api/runs/{run_id}"

        detail = wait_for(client, run_id, finished)
        assert detail["summary"]["status"] == "failed"
        assert detail["summary"]["error"] == "The model is down."
        assert detail["live"] is None
        events = client.get(f"/api/runs/{run_id}/events").json()
        assert [e["type"] for e in events] == ["start", "baseline", "activity", "error"]
        assert [e["seq"] for e in events] == [1, 2, 3, 4]
        assert client.get(f"/api/runs/{run_id}/events", params={"after": 3}).json() == events[3:]
        assert [r["id"] for r in client.get("/api/runs").json()] == [run_id]
    assert not (project / "src/math.test.ts").exists()


def test_cancelling_stops_the_run_and_restores_the_test_file(project):
    with client_for(project, slow_llm) as client:
        run_id = client.post("/api/runs", json={"source": "src/math.ts"}).json()["summary"]["id"]
        live = wait_for(client, run_id, lambda d: (d["live"] or {}).get("activity") is not None)["live"]
        assert live["activity"] == "Generating tests with slow"

        # One run at a time, and nothing may change the project under it.
        assert client.get("/api/project").json()["active_run"] == run_id
        assert client.post("/api/runs", json={"source": "src/math.ts"}).status_code == 409
        assert client.post("/api/project/init").status_code == 409
        assert client.delete(f"/api/runs/{run_id}").status_code == 409

        cancelling = client.post(f"/api/runs/{run_id}/cancel")
        assert cancelling.status_code == 202
        live = cancelling.json()["live"]
        assert live is None or live["cancel_requested"]  # None if the run already cleaned up

        detail = wait_for(client, run_id, finished, timeout=10)
        assert detail["summary"]["status"] == "cancelled"
        assert detail["report"]["error"] == "Interrupted"
        types = [e["type"] for e in client.get(f"/api/runs/{run_id}/events").json()]
        assert types[-2:] == ["cancelling", "cancelled"]
        assert client.post(f"/api/runs/{run_id}/cancel").status_code == 409
        assert client.get("/api/project").json()["active_run"] is None

        assert client.delete(f"/api/runs/{run_id}").status_code == 204
        assert client.get(f"/api/runs/{run_id}").status_code == 404
    assert not (project / "src/math.test.ts").exists()


def test_stopping_the_server_cancels_the_run_in_progress(project):
    with client_for(project, slow_llm) as client:
        run_id = client.post("/api/runs", json={"source": "src/math.ts"}).json()["summary"]["id"]
        wait_for(client, run_id, lambda d: (d["live"] or {}).get("activity") is not None)
    report = json.loads((project / ".regress/runs" / run_id / "report.json").read_text())
    assert report["status"] == "cancelled"


def test_the_server_cleans_up_after_a_run_that_was_killed(project):
    test_file = project / "src/math.test.ts"
    with client_for(project, slow_llm) as client:
        run_id = client.post("/api/runs", json={"source": "src/math.ts"}).json()["summary"]["id"]
        wait_for(client, run_id, lambda d: (d["live"] or {}).get("activity") is not None)
        test_file.write_text("a candidate the run was checking\n")
        os.kill(client.app.state.manager.active().process.pid, signal.SIGKILL)

        detail = wait_for(client, run_id, finished, timeout=10)
        assert detail["summary"]["status"] == "failed"
        assert detail["summary"]["error"] == "The run stopped unexpectedly (exit code -9)."
        assert client.get(f"/api/runs/{run_id}/events").json()[-1]["type"] == "error"
    assert not test_file.exists()


# --- reading saved runs ----------------------------------------------------------------------


def _mutant(mutant_id: str, status: MutantStatus, line: int, find: str, replacement: str) -> Mutant:
    start = SOURCE.splitlines()[line - 1].index(find) + 1
    return Mutant(
        id=mutant_id,
        mutator="ArithmeticOperator",
        status=status,
        original=find,
        replacement=replacement,
        start_line=line,
        start_column=start,
        end_line=line,
        end_column=start + len(find),
    )


@pytest.fixture
def saved_run(project: Path) -> str:
    """A finished run on disk, as the pipeline leaves it, with an improvement that made things worse."""
    run_dir = project / ".regress/runs/20260926-120000-math"
    (run_dir / "tests").mkdir(parents=True)
    (run_dir / "stryker").mkdir()
    (run_dir / "llm").mkdir()
    survivor = _mutant("1", MutantStatus.SURVIVED, 1, "a + b", "a - b")
    killed = _mutant("2", MutantStatus.KILLED, 2, "-a", "a")
    versions = {
        "0-baseline.test.ts": 'test("adds", () => {});\n',
        "1-generated.test.ts": 'test("adds", () => {});\ntest("negates", () => {});\n',
        "2-improved.test.ts": 'test("adds", () => {});\ntest("negates", () => {});\ntest("zero", () => {});\n',
    }
    for name, content in versions.items():
        (run_dir / "tests" / name).write_text(content)
    for index in (1, 2, 3):
        stryker = {"files": {"src/math.ts": {"source": SOURCE, "mutants": []}}}
        (run_dir / "stryker" / f"mutation-{index}.json").write_text(json.dumps(stryker))
    (run_dir / "llm/01-generated-attempt1.prompt.md").write_text("Write tests for src/math.ts")
    (run_dir / "llm/01-generated-attempt1.response.json").write_text(
        json.dumps(
            {
                "summary": "Pins negation.",
                "new_tests": ["negates"],
                "equivalent_mutants": [],
                "test_file": versions["1-generated.test.ts"],
                "usage": {"input_tokens": 120, "output_tokens": 40},
            }
        )
    )

    def stage(kind, label, snapshot, tests, index, mutants, **extra) -> Stage:
        return Stage(
            kind=kind,
            label=label,
            test_file_snapshot=f"tests/{snapshot}" if snapshot else None,
            test_count=len(tests),
            test_names=tests,
            mutation=MutationRun(index=index, mutants=mutants) if index else None,
            **extra,
        )

    report = RunReport(
        id=run_dir.name,
        created_at=datetime(2026, 9, 26, 12, 0).astimezone(),
        project_root=str(project),
        source_file="src/math.ts",
        test_file="src/math.test.ts",
        test_file_existed=True,
        model="gpt-6-luna",
        status="completed",
        stages=[
            stage(
                "baseline",
                "Existing tests",
                "0-baseline.test.ts",
                ["adds"],
                1,
                [survivor, survivor.model_copy(update={"id": "2"})],
            ),
            stage("generated", "Generated tests", "1-generated.test.ts", ["adds", "negates"], 2, [survivor, killed]),
            stage(
                "improved",
                "Improved tests (round 1)",
                "2-improved.test.ts",
                ["adds", "negates", "zero"],
                3,
                [
                    survivor.model_copy(update={"status": MutantStatus.NO_COVERAGE}),
                    killed.model_copy(update={"status": MutantStatus.SURVIVED}),
                ],
                equivalent_mutants=["1"],
            ),
            stage("improved", "Improved tests (round 2)", None, [], 0, [], rejected=True, problems=["No new tests."]),
        ],
        kept_stage="Generated tests",
        duration_seconds=42.0,
    )
    (run_dir / "report.json").write_text(report.model_dump_json(indent=2))
    return run_dir.name


def test_saved_runs_are_listed_with_their_scores(client, saved_run):
    [summary] = client.get("/api/runs").json()
    assert summary["id"] == saved_run
    assert (summary["baseline_score"], summary["kept_score"], summary["improvement"]) == (0.0, 50.0, 0.0)
    assert (summary["tests_before"], summary["tests_after"], summary["active"]) == (1, 2, False)
    assert client.get("/api/runs", params={"source": "src/other.ts"}).json() == []

    detail = client.get(f"/api/runs/{saved_run}").json()
    assert detail["live"] is None
    assert [s["label"] for s in detail["report"]["stages"]][:2] == ["Existing tests", "Generated tests"]
    assert detail["report"]["stages"][1]["mutation"]["score"] == 50.0
    assert client.get(f"/api/runs/{saved_run}/events").json() == []  # started from the CLI


def test_unknown_runs_are_not_found(client, saved_run):
    for run_id in ("20990101-000000-nope", "..", "..%2F..%2Fpackage.json"):
        assert client.get(f"/api/runs/{run_id}").status_code == 404, run_id
    assert client.get(f"/api/runs/{saved_run}/stages/9/tests").status_code == 404


def test_stage_diffs_compare_with_the_tests_each_stage_started_from(client, saved_run):
    generated = client.get(f"/api/runs/{saved_run}/stages/1/diff").json()
    assert (generated["before_stage"], generated["added_tests"]) == (0, ["negates"])
    assert '+test("negates", () => {});' in generated["unified"]

    # The worse improvement started from the generated tests, and was not kept.
    improved = client.get(f"/api/runs/{saved_run}/stages/2/diff").json()
    assert (improved["before_label"], improved["added_tests"]) == ("Generated tests", ["zero"])

    whole = client.get(f"/api/runs/{saved_run}/diff").json()
    assert (whole["stage"], whole["before_stage"], whole["after"]) == (1, 0, generated["after"])

    assert client.get(f"/api/runs/{saved_run}/stages/3/tests").status_code == 404  # rejected
    tests = client.get(f"/api/runs/{saved_run}/stages/2/tests").json()
    assert tests["test_count"] == 3 and tests["content"].count("test(") == 3


def test_mutants_come_with_the_changed_source_line(client, saved_run):
    undetected = client.get(f"/api/runs/{saved_run}/stages/1/mutants").json()
    assert (undetected["total"], len(undetected["mutants"])) == (2, 1)
    [mutant] = undetected["mutants"]
    assert mutant["original_line"] == "export const add = (a: number, b: number) => a + b;"
    assert mutant["mutated_line"] == "export const add = (a: number, b: number) => a - b;"
    assert mutant["equivalent"] and not mutant["detected"]

    detected = client.get(f"/api/runs/{saved_run}/stages/1/mutants", params={"status": "detected"}).json()
    assert [m["id"] for m in detected["mutants"]] == ["2"]
    assert client.get(f"/api/runs/{saved_run}/stages/3/mutants").status_code == 404  # never mutation-tested


def test_model_calls_and_artifacts_can_be_read(client, saved_run):
    [call] = client.get(f"/api/runs/{saved_run}/llm").json()
    assert (call["name"], call["kind"], call["attempt"], call["input_tokens"]) == (
        "01-generated-attempt1",
        "generated",
        1,
        120,
    )
    detail = client.get(f"/api/runs/{saved_run}/llm/{call['name']}").json()
    assert detail["prompt"] == "Write tests for src/math.ts" and "negates" in detail["test_file"]
    assert client.get(f"/api/runs/{saved_run}/llm/nope").status_code == 404

    paths = [a["path"] for a in client.get(f"/api/runs/{saved_run}/artifacts").json()]
    assert "report.json" in paths and "tests/1-generated.test.ts" in paths
    report = client.get(f"/api/runs/{saved_run}/artifacts/report.json")
    assert report.headers["content-type"] == "application/json" and report.json()["id"] == saved_run
    snapshot = client.get(f"/api/runs/{saved_run}/artifacts/tests/0-baseline.test.ts")
    assert snapshot.headers["content-type"].startswith("text/plain")
    assert client.get(f"/api/runs/{saved_run}/artifacts/..%2F..%2F..%2Fpackage.json").status_code == 404


def test_the_event_stream_of_a_finished_run_replays_its_log_and_ends(client, project, saved_run):
    log = project / ".regress/runs" / saved_run / "events.jsonl"
    log.write_text(
        '{"seq": 1, "time": "2026-09-26T12:00:00+02:00", "type": "start", "message": "Analyzing src/math.ts"}\n'
        '{"seq": 2, "time": "2026-09-26T12:00:01+02:00", "type": "note", "message": "Done"}\n'
        "{half a line\n"
    )
    with client.stream("GET", f"/api/runs/{saved_run}/events/stream", headers={"Last-Event-ID": "1"}) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        body = response.read().decode()
    assert body.count("event: run-event") == 1 and "id: 2" in body and "Done" in body
    assert body.rstrip().splitlines()[-1].startswith("data: ") and "event: end" in body
