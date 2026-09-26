from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from test_api import NO_MODEL
from test_api import client as client
from test_api import project as project
from test_api import saved_run as saved_run
from typer.testing import CliRunner

from regress.api import create_app
from regress.api.app import MAX_REQUEST_BYTES, RequestLimits
from regress.cli import app
from regress.errors import ProjectError
from regress.project import load_project


def test_readiness_is_safe_to_expose_and_checks_storage(client, project, monkeypatch):
    response = client.get("/api/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "project": True, "storage": True}
    assert str(project) not in response.text

    def full_disk(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("regress.api.routes.TemporaryFile", full_disk)
    response = client.get("/api/ready")
    assert response.status_code == 503
    assert response.json()["storage"] is False


def test_readiness_requires_credentials_for_real_model(project):
    with TestClient(create_app(project), base_url="http://127.0.0.1") as client:
        assert client.get("/api/health").status_code == 200
        response = client.get("/api/ready")
        assert response.status_code == 503
        assert response.json()["project"] is False


def test_failed_workspace_cleanup_makes_project_unready(client):
    client.app.state.manager._problem = "Workspace cleanup failed; restart required."
    assert client.get("/api/ready").status_code == 503
    assert client.get("/api/project").json()["problems"] == ["Workspace cleanup failed; restart required."]
    assert client.post("/api/runs", json={"source": "src/math.ts"}).status_code == 503


def test_large_requests_are_rejected_before_parsing(client):
    response = client.post("/api/runs", content=b"x" * (MAX_REQUEST_BYTES + 1))
    assert response.status_code == 413
    assert len(response.headers["x-request-id"]) == 32
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/runs").json() == []


def test_chunked_requests_cannot_bypass_body_limit():
    async def check():
        received = iter(
            [
                {"type": "http.request", "body": b"a" * MAX_REQUEST_BYTES, "more_body": True},
                {"type": "http.request", "body": b"b", "more_body": False},
            ]
        )
        output = []

        async def receive():
            return next(received)

        async def send(message):
            output.append(message)

        async def downstream(scope, receive, send):
            pytest.fail("Oversized body reached the application")

        await RequestLimits(downstream)({"type": "http", "method": "POST", "headers": []}, receive, send)
        assert output[0]["status"] == 413

    asyncio.run(check())


def test_unexpected_errors_are_logged_without_exposing_details(project, monkeypatch, caplog):
    def broken(manager):
        raise RuntimeError("private diagnostic")

    monkeypatch.setattr("regress.api.project.project_info", broken)
    with TestClient(
        create_app(project, llm_factory=NO_MODEL), base_url="http://127.0.0.1", raise_server_exceptions=False
    ) as client:
        response = client.get("/api/project", headers={"X-Request-ID": "untrusted"})
    assert response.status_code == 500
    assert "private diagnostic" not in response.text
    assert "private diagnostic" in caplog.text
    assert response.headers["x-request-id"] in caplog.text
    assert response.headers["x-request-id"] != "untrusted"


def test_corrupt_event_log_reconnect_uses_sequence_numbers(client, project, saved_run):
    log = project / ".regress/runs" / saved_run / "events.jsonl"
    log.write_text(
        '{"seq":1,"time":"2026-09-26T12:00:00Z","type":"start","message":"start"}\n'
        "damaged second event\n"
        '{"seq":3,"time":"2026-09-26T12:00:02Z","type":"note","message":"third"}\n'
    )
    response = client.get(f"/api/runs/{saved_run}/events", params={"after": 2})
    assert [event["seq"] for event in response.json()] == [3]
    response = client.get(f"/api/runs/{saved_run}/events/stream", headers={"Last-Event-ID": "2"})
    assert "id: 3" in response.text
    assert response.text.count("event: run-event") == 1
    assert "event: end" in response.text


def test_invalid_event_cursor_cannot_crash_stream(client, saved_run):
    response = client.get(f"/api/runs/{saved_run}/events/stream", headers={"Last-Event-ID": "9" * 5000})
    assert response.status_code == 200
    assert "event: end" in response.text


def test_corrupt_llm_artifacts_do_not_break_lists(client, project, saved_run):
    directory = project / ".regress/runs" / saved_run / "llm"
    (directory / "02-improved-attempt1.response.json").write_text('{"usage": null}')
    assert len(client.get(f"/api/runs/{saved_run}/llm").json()) == 1
    assert client.get(f"/api/runs/{saved_run}/llm/02-improved-attempt1").status_code == 404


def test_artifact_symlinks_cannot_read_outside_run(client, project, saved_run):
    run_dir = project / ".regress/runs" / saved_run
    secret = project / "private.json"
    secret.write_text(json.dumps({"summary": "private data"}))
    link = run_dir / "llm/02-improved-attempt1.response.json"
    link.symlink_to(secret)
    assert len(client.get(f"/api/runs/{saved_run}/llm").json()) == 1
    assert client.get(f"/api/runs/{saved_run}/llm/02-improved-attempt1").status_code == 404
    artifacts = client.get(f"/api/runs/{saved_run}/artifacts").json()
    assert link.relative_to(run_dir).as_posix() not in [entry["path"] for entry in artifacts]


def test_source_listing_ignores_symlinks_and_bad_text_returns_422(client, project):
    (project / "src/broken.ts").write_bytes(b"\xff")
    (project / "src/link.ts").symlink_to(project.parent / "missing.ts")
    paths = [source["path"] for source in client.get("/api/project/sources").json()]
    assert "src/link.ts" not in paths
    assert client.get("/api/project/files/src/broken.ts").status_code == 422
    assert client.get("/api/project/sources/src/broken.ts").status_code == 422


def test_automatic_test_path_cannot_escape_project(project):
    outside = project.parent / "private.test.ts"
    outside.write_text("unchanged")
    (project / "src/math.test.ts").symlink_to(outside)
    with pytest.raises(ProjectError, match="outside the project"):
        load_project(project / "src/math.ts")
    assert outside.read_text() == "unchanged"


def test_explicit_test_path_cannot_overwrite_config(project):
    with pytest.raises(ProjectError, match="JavaScript or TypeScript"):
        load_project(project / "src/math.ts", project / ".env")


@pytest.mark.parametrize("field,value", [("rounds", True), ("rounds", "2"), ("generate", "false")])
def test_run_requests_require_correct_json_types(client, field, value):
    response = client.post("/api/runs", json={"source": "src/math.ts", field: value})
    assert response.status_code == 422


def test_remote_bind_requires_explicit_opt_in():
    result = CliRunner().invoke(app, ["serve", "--host", "0.0.0.0"])
    assert result.exit_code == 1
    assert "--allow-remote" in result.output
