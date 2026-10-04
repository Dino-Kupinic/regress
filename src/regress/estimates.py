"""What a run will cost, before it starts: from the prompt Regress would send and the run's options."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from regress.errors import ProjectError
from regress.pipeline import RunOptions
from regress.pricing import Estimate, Price, estimate, run_price, token_factor
from regress.project import Project
from regress.prompts import INSTRUCTIONS, PromptContext

if TYPE_CHECKING:
    from regress.config import Settings


def run_options(settings: Settings, model: str, **overrides: bool) -> RunOptions:
    """The pipeline options for a run with `model`, including its price and budget."""
    price = run_price(settings, model)
    if settings.max_cost is not None and price is None:
        raise ProjectError(
            f"max_cost needs a price for {model}, and Regress doesn't know one. Add it to regress.toml:\n"
            f'  [prices."{model}"]\n  input = 1.0    # USD per million tokens\n  output = 4.0'
        )
    return RunOptions(
        rounds=settings.rounds,
        max_repairs=settings.max_repairs,
        max_mutants=settings.max_mutants,
        vitest_timeout=settings.vitest_timeout,
        stryker_timeout=settings.stryker_timeout,
        price=price,
        max_cost=settings.max_cost,
        **overrides,
    )


@dataclass(frozen=True)
class RunEstimate:
    source_file: str
    estimate: Estimate


def estimate_project(
    project: Project, options: RunOptions, model: str, price: Price | None, max_output_tokens: int
) -> RunEstimate:
    ctx = PromptContext.from_project(project)
    code = ctx.source + "".join(text for _, text in ctx.related)
    tests = project.test_file.read_text(encoding="utf-8", errors="replace") if project.test_file.is_file() else None
    return RunEstimate(
        project.source_rel,
        estimate(
            INSTRUCTIONS,
            code,
            tests,
            rounds=options.rounds,
            max_repairs=options.max_repairs,
            max_mutants=options.max_mutants,
            generate=options.generate or tests is None,
            max_output_tokens=max_output_tokens,
            price=price,
            factor=token_factor(model),
        ),
    )


def total(estimates: list[RunEstimate]) -> Estimate | None:
    """Several files' estimates added up; the USD totals are None unless every file has one."""
    if not estimates:
        return None
    parts = [e.estimate for e in estimates]
    priced = all(p.expected_usd is not None for p in parts)
    return Estimate(
        input_tokens=sum(p.input_tokens for p in parts),
        output_tokens=sum(p.output_tokens for p in parts),
        calls=sum(p.calls for p in parts),
        max_input_tokens=sum(p.max_input_tokens for p in parts),
        max_output_tokens=sum(p.max_output_tokens for p in parts),
        max_calls=sum(p.max_calls for p in parts),
        expected_usd=sum(p.expected_usd or 0 for p in parts) if priced else None,
        max_usd=sum(p.max_usd or 0 for p in parts) if priced else None,
    )
