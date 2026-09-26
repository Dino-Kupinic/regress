"""Runs started over HTTP, against real Vitest and Stryker, with a scripted model."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

import pytest
from conftest import EXAMPLES, ScriptedFactory, requires_examples
from fastapi.testclient import TestClient
from test_pipeline import BASELINE, ONE_SHOT, ORACLE

from regress.api import create_app
from regress.evaluation import sandbox

pytestmark = [pytest.mark.integration, requires_examples]


@pytest.fixture
def root() -> Iterator[Path]:
    with sandbox(EXAMPLES) as path:
        yield path


def server_sent_events(lines: Iterable[str]) -> Iterator[tuple[str, dict]]:
    kind, data = "message", []
    for line in lines:
        if line.startswith("event: "):
            kind = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data.append(line.removeprefix("data: "))
        elif not line and data:
            yield kind, json.loads("\n".join(data))
            kind, data = "message", []


def test_a_run_streams_its_progress_and_leaves_a_full_report(root):
    app = create_app(root, llm_factory=ScriptedFactory(ONE_SHOT, ORACLE))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        started = client.post("/api/runs", json={"source": "src/cart.ts", "baseline": True})
        assert started.status_code == 202
        run_id = started.json()["summary"]["id"]

        events, activities, end = [], set(), None
        with client.stream("GET", f"/api/runs/{run_id}/events/stream") as stream:
            for kind, data in server_sent_events(stream.iter_lines()):
                if kind == "run-event":
                    events.append(data)
                elif kind == "live":
                    activities.add(data["activity"])
                elif kind == "end":
                    end = data

        assert end is not None and end["status"] == "completed" and not end["active"]
        assert end["kept_stage"] == "Improved tests"
        types = [e["type"] for e in events]
        assert types[0] == "start" and types[-1] == "finished"
        assert types.count("mutation") == 3
        assert {"baseline", "generated", "improving", "improved"} <= set(types)
        assert "Running mutation testing on src/cart.ts" in activities
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
        assert client.get(f"/api/runs/{run_id}/events").json() == events

        summary = client.get(f"/api/runs/{run_id}").json()["summary"]
        assert summary["baseline_score"] < summary["reference_score"] < summary["kept_score"]
        assert summary["improvement"] == pytest.approx(summary["kept_score"] - summary["reference_score"])

        diff = client.get(f"/api/runs/{run_id}/diff").json()
        assert (diff["before"], diff["after"]) == (BASELINE, ORACLE)
        assert client.get(f"/api/runs/{run_id}/stages/2/diff").json()["before"] == ONE_SHOT

        survivors = client.get(f"/api/runs/{run_id}/stages/1/mutants").json()["mutants"]
        assert survivors and all(not m["detected"] for m in survivors)
        assert any(m["original_line"] and m["mutated_line"] for m in survivors)

        calls = client.get(f"/api/runs/{run_id}/llm").json()
        assert [c["kind"] for c in calls] == ["generated", "improved"]
        prompt = client.get(f"/api/runs/{run_id}/llm/{calls[1]['name']}").json()["prompt"]
        assert "Mutants the current tests fail to detect" in prompt
    assert (root / "test/cart.test.ts").read_text() == ORACLE


def test_cancelling_stops_stryker_at_once(root):
    with TestClient(create_app(root, llm_factory=ScriptedFactory()), base_url="http://127.0.0.1") as client:
        run_id = client.post("/api/runs", json={"source": "src/cart.ts", "baseline": True}).json()["summary"]["id"]
        deadline = time.monotonic() + 60
        while (client.get(f"/api/runs/{run_id}").json()["live"] or {}).get("activity") != (
            "Running mutation testing on src/cart.ts"
        ):
            assert time.monotonic() < deadline, "Stryker never started"
            time.sleep(0.1)
        time.sleep(1)  # let Stryker get going

        cancelled_at = time.monotonic()
        client.post(f"/api/runs/{run_id}/cancel")
        while client.get(f"/api/runs/{run_id}").json()["summary"]["active"]:
            assert time.monotonic() - cancelled_at < 10, "the run did not stop"
            time.sleep(0.05)

        assert client.get(f"/api/runs/{run_id}").json()["summary"]["status"] == "cancelled"
    leftovers = subprocess.run(["pgrep", "-f", run_id], capture_output=True, text=True).stdout
    assert leftovers == "", "Stryker is still running"
    assert (root / "test/cart.test.ts").read_text() == BASELINE
