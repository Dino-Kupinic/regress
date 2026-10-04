"""Prices, cost from usage, estimates before a run, budgets, and cost in evaluations and the API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from regress.api import create_app
from regress.config import load_settings, save_user_settings
from regress.evaluation import EvalResult, ModuleResult, StageResult
from regress.models import RunReport, TokenUsage
from regress.pricing import (
    PRICES,
    Price,
    cost,
    estimate,
    format_usd,
    price_for,
    run_price,
    token_factor,
)
from regress.ui import _usage_line


def test_prices_come_from_config_first_then_the_bundled_list():
    assert price_for("openai", "gpt-6-luna") == Price(input=0.10, output=0.50, cached_input=0.01)
    assert price_for("anthropic", "claude-opus-5-5") == Price(input=4.0, output=20.0, cached_input=0.20)
    # Dated snapshots cost what their alias costs.
    assert price_for("openai", "gpt-6-luna-2026-09-14") == PRICES["openai"]["gpt-6-luna"]
    assert price_for("anthropic", "claude-haiku-4-5-20251001") == PRICES["anthropic"]["claude-haiku-4-5"]
    custom = Price(input=1, output=2)
    assert price_for("openai", "gpt-6-luna", {"gpt-6-luna": custom}) == custom
    assert price_for("openai-compatible", "llama3.1:8b") is None
    assert price_for("openai-compatible", "llama3.1:8b", {"llama3.1:8b": custom}) == custom
    assert price_for("openai", "claude-opus-5-5") is None  # a price belongs to its provider


def test_cost_charges_cached_input_at_its_own_rate():
    price = Price(input=2.0, output=10.0, cached_input=0.2)
    assert cost(price, 1_000_000, 0) == pytest.approx(2.0)
    assert cost(price, 1_000_000, 100_000, cached_input_tokens=500_000) == pytest.approx(1.0 + 0.1 + 1.0)
    assert cost(Price(input=2.0, output=10.0), 1_000_000, 0, cached_input_tokens=500_000) == pytest.approx(2.0)


def test_format_usd_keeps_small_amounts_readable():
    assert [format_usd(v) for v in (None, 0, 0.00314, 0.0421, 12.3)] == ["—", "$0.00", "$0.0031", "$0.04", "$12.30"]


def run_estimate(**options):
    defaults = dict(rounds=1, max_repairs=2, max_mutants=40, generate=True, max_output_tokens=32768)
    return estimate(
        "instructions " * 100, "code " * 2000, "tests " * 500, price=Price(input=1, output=4), **(defaults | options)
    )


def test_estimate_grows_with_rounds_and_repairs_and_bounds_the_worst_case():
    base = run_estimate()
    assert base.calls == 2 and base.max_calls == 6  # generation + one round, up to 3 attempts each
    assert 0 < base.expected_usd < base.max_usd
    assert run_estimate(rounds=3).expected_usd > base.expected_usd
    assert run_estimate(rounds=0).calls == 1
    assert run_estimate(generate=False).calls == 1
    assert run_estimate(max_repairs=0).max_calls == 2
    assert run_estimate(max_mutants=200).expected_usd > base.expected_usd
    # The worst case assumes every response fills the output budget, so a smaller budget lowers it.
    assert run_estimate(max_output_tokens=4096).max_usd < base.max_usd


def test_estimate_without_a_price_still_counts_tokens():
    unpriced = estimate("i", "code " * 100, None, rounds=1, max_repairs=0, max_mutants=10, generate=True,
                        max_output_tokens=8192, price=None)  # fmt: skip
    assert unpriced.expected_usd is None and unpriced.input_tokens > 0


def test_newer_claude_models_count_more_tokens():
    assert token_factor("claude-opus-5-5") == 1.3 and token_factor("claude-sonnet-4-6") == 1.0
    assert token_factor("gpt-6-luna") == 1.0


def test_prices_and_budget_are_settings(tmp_path):
    (tmp_path / "regress.toml").write_text('max_cost = 0.5\n[prices."my-model"]\ninput = 1\noutput = 4\n')
    settings = load_settings(tmp_path, model="openai-compatible:my-model")
    assert settings.max_cost == 0.5 and run_price(settings) == Price(input=1.0, output=4.0)
    assert load_settings(tmp_path, max_cost=2.0).max_cost == 2.0

    save_user_settings(prices={"other": {"input": 0.5, "output": 1}})
    assert load_settings(None).prices == {"other": Price(input=0.5, output=1.0)}  # written back as valid TOML


def test_usage_line_shows_cost_and_budget():
    report = RunReport(
        id="r",
        created_at="2026-10-04T12:00:00+00:00",
        project_root="/p",
        source_file="a.ts",
        test_file="a.test.ts",
        test_file_existed=True,
        model="m",
        usage=TokenUsage(input_tokens=1200, output_tokens=300, cached_input_tokens=200, calls=2),
        cost_usd=0.0042,
        max_cost=0.5,
    )
    assert _usage_line(report) == (
        "Cost $0.0042 of $0.50 · 2 model calls · 1,200 input / 300 output tokens (200 cached)"
    )
    report.cost_usd = None
    assert _usage_line(report).startswith("2 model calls")


def test_eval_cost_per_mutant_and_per_bug():
    def module(name: str, usd: float | None, killed: int, caught: int) -> ModuleResult:
        stages = [StageResult(label=label, score=50.0, caught=[f"b{i}" for i in range(caught)], missed=["x"])
                  for label in ("Existing tests", "One-shot AI", "Regress")]  # fmt: skip
        return ModuleResult(module=name, stages=stages, cost_usd=usd, killed=killed)

    result = EvalResult(created_at="2026-10-04T12:00:00+00:00", mode="regress", model="m", rounds=1,
                        modules=[module("a", 0.02, 30, 2), module("b", 0.04, 30, 4)])  # fmt: skip
    spent = result.cost
    assert spent.usd == pytest.approx(0.06)
    assert spent.per_mutant_killed == pytest.approx(0.001) and spent.per_bug_caught == pytest.approx(0.01)
    result.modules[1].cost_usd = None  # an unpriced module: no total that leaves it out silently
    assert result.cost is None


@pytest.fixture
def api(js_project: Path):
    (js_project / "src/math.ts").write_text("export const add = (a: number, b: number) => a + b;\n" * 20)
    with TestClient(create_app(js_project), base_url="http://127.0.0.1") as client:
        yield client


def test_api_estimates_before_a_run(api):
    estimate = api.post("/api/estimate", json={"sources": ["src/math.ts"], "rounds": 2}).json()
    assert estimate["model"] == "gpt-6-luna" and estimate["price"]["input"] == 0.1
    assert estimate["calls"] == 3 and 0 < estimate["expected_usd"] < estimate["max_usd"]
    assert [f["source_file"] for f in estimate["files"]] == ["src/math.ts"]

    unknown = api.post("/api/estimate", json={"sources": ["src/math.ts"], "model": "openai-compatible:x"}).json()
    assert unknown["price"] is None and unknown["expected_usd"] is None and unknown["input_tokens"] > 0
    assert api.post("/api/estimate", json={"sources": ["../x.ts"]}).status_code == 400
    assert api.get("/api/evaluations/estimate").status_code == 400  # no hidden-bugs suites here


def test_api_refuses_a_budget_it_cannot_enforce(js_project, monkeypatch):
    from conftest import ScriptedFactory

    (js_project / "src/math.ts").write_text("export const add = (a: number, b: number) => a + b;\n")
    with TestClient(create_app(js_project, llm_factory=ScriptedFactory()), base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/runs", json={"source": "src/math.ts", "model": "openai-compatible:local", "max_cost": 1.0}
        )
    assert response.status_code == 400 and "max_cost needs a price" in response.json()["detail"]
