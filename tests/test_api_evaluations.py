"""Evaluation routes return saved results and expose the background job."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from regress.api import create_app
from regress.api import evaluations as evaluation_api


def test_evaluation_routes_read_suites_and_results(js_project: Path):
    suites = js_project / "hidden-bugs"
    suites.mkdir()
    (suites / "math.json").write_text(
        json.dumps(
            {
                "source": "src/math.ts",
                "test": "test/math.test.ts",
                "oracle": "oracle/math.ts",
                "bugs": [{"id": "wrong-sum", "description": "Uses subtraction", "find": "a + b", "replace": "a - b"}],
            }
        )
    )
    saved = js_project / ".regress" / "eval" / "20260926-100000"
    saved.mkdir(parents=True)
    (saved / "eval.json").write_text(
        json.dumps(
            {
                "created_at": "2026-09-26T10:00:00Z",
                "mode": "oracle",
                "modules": [
                    {
                        "module": "math",
                        "stages": [{"label": "Oracle", "score": 100, "caught": ["wrong-sum"]}],
                    }
                ],
                "output_dir": str(saved),
            }
        )
    )
    with TestClient(create_app(js_project), base_url="http://127.0.0.1") as client:
        assert client.get("/api/evaluations/suites").json()[0]["name"] == "math"
        assert client.get("/api/evaluations").json()[0]["modules"][0]["stages"][0]["caught"] == ["wrong-sum"]
        assert client.get("/api/evaluations/job").json()["status"] == "idle"
        assert client.post("/api/evaluations", json={"mode": "invalid"}).status_code == 422


def test_evaluation_start_requires_suites(js_project: Path):
    with TestClient(create_app(js_project), base_url="http://127.0.0.1") as client:
        assert client.get("/api/evaluations/suites").json() == []
        assert client.post("/api/evaluations", json={"mode": "oracle"}).status_code == 400


def test_evaluation_start_launches_background_command(js_project: Path, monkeypatch):
    suites = js_project / "hidden-bugs"
    suites.mkdir()
    (suites / "math.json").write_text(
        json.dumps(
            {
                "source": "src/math.ts",
                "test": "test/math.test.ts",
                "bugs": [],
            }
        )
    )
    commands = []

    class Process:
        def poll(self):
            return None

        def send_signal(self, signum):
            pass

        def wait(self, timeout=None):
            return 0

    def launch(command, **kwargs):
        commands.append(command)
        return Process()

    monkeypatch.setattr(evaluation_api.subprocess, "Popen", launch)
    with TestClient(create_app(js_project), base_url="http://127.0.0.1") as client:
        response = client.post("/api/evaluations", json={"mode": "oracle"})
        assert response.status_code == 202
        assert response.json()["status"] == "running"
        assert client.post("/api/evaluations", json={"mode": "oracle"}).status_code == 409
    assert commands[0][-1] == "--oracle"
