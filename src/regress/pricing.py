"""What model calls cost: a bundled price list, overridable in config, and estimates before a run."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from regress.config import Settings

# Standard rates in USD per million tokens (short context: OpenAI charges more above 272K input tokens,
# which Regress prompts don't reach). Prices change: override them with [prices] in regress.toml.
PRICES_AS_OF = "2026-10-04"
PRICE_SOURCES = {
    "openai": "https://developers.openai.com/api/docs/pricing",
    "anthropic": "https://platform.claude.com/docs/en/about-claude/pricing",
}


class Price(BaseModel):
    """USD per million tokens. `cached_input` is what a cache hit costs, when the provider discounts it."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    input: float = Field(ge=0, le=10_000)
    output: float = Field(ge=0, le=10_000)
    cached_input: float | None = Field(default=None, ge=0, le=10_000)


def _p(input: float, output: float, cached_input: float | None = None) -> Price:
    return Price(input=input, output=output, cached_input=cached_input)


PRICES: dict[str, dict[str, Price]] = {
    "openai": {
        "gpt-6-astra": _p(10.00, 50.00, 1.00),
        "gpt-6.1-sol": _p(2.00, 10.00, 0.10),
        "gpt-6-sol": _p(2.00, 10.00, 0.20),
        "gpt-6-luna": _p(0.10, 0.50, 0.01),
        "gpt-5.6-sol": _p(4.00, 20.00, 0.40),
        "gpt-5.6-terra": _p(2.00, 12.00, 0.20),
        "gpt-5.6-luna": _p(0.20, 1.20, 0.02),
        "gpt-5.5": _p(5.00, 30.00, 0.50),
        "gpt-5.5-pro": _p(30.00, 180.00),
        "gpt-5.4": _p(2.50, 15.00, 0.25),
        "gpt-5.4-mini": _p(0.75, 4.50, 0.075),
        "gpt-5.4-nano": _p(0.20, 1.25, 0.02),
        "gpt-5.4-pro": _p(30.00, 180.00),
        "gpt-5.2": _p(1.75, 14.00, 0.175),
        "gpt-5.1": _p(1.25, 10.00, 0.125),
        "gpt-5": _p(1.25, 10.00, 0.125),
        "gpt-5-mini": _p(0.25, 2.00, 0.025),
        "gpt-5-nano": _p(0.05, 0.40, 0.005),
        "gpt-4.1": _p(2.00, 8.00, 0.50),
        "gpt-4.1-mini": _p(0.40, 1.60, 0.10),
        "gpt-4.1-nano": _p(0.10, 0.40, 0.025),
        "o3": _p(2.00, 8.00, 0.50),
        "o4-mini": _p(1.10, 4.40, 0.275),
    },
    "anthropic": {
        "claude-fable-5-1": _p(10.00, 50.00, 0.25),
        "claude-fable-5": _p(10.00, 50.00, 1.00),
        "claude-opus-5-5": _p(4.00, 20.00, 0.20),
        "claude-opus-5": _p(5.00, 25.00, 0.50),
        "claude-opus-4-8": _p(5.00, 25.00, 0.50),
        "claude-opus-4-7": _p(5.00, 25.00, 0.50),
        "claude-opus-4-6": _p(5.00, 25.00, 0.50),
        "claude-opus-4-5": _p(5.00, 25.00, 0.50),
        "claude-sonnet-5-5": _p(2.00, 10.00, 0.20),
        "claude-sonnet-5": _p(2.00, 10.00, 0.20),
        "claude-sonnet-4-6": _p(3.00, 15.00, 0.30),
        "claude-sonnet-4-5": _p(3.00, 15.00, 0.30),
        "claude-haiku-4-5": _p(1.00, 5.00, 0.10),
    },
}

# Dated snapshots cost what their alias costs: gpt-6-luna-2026-09-14, claude-haiku-4-5-20251001.
_SNAPSHOT = re.compile(r"-(?:\d{4}-\d{2}-\d{2}|\d{8})$")


def run_price(settings: Settings, model: str | None = None) -> Price | None:
    """The price a run with these settings pays, for `model` or the configured one."""
    model = model or settings.model
    return price_for(settings.provider, model, settings.prices) if model else None


def price_for(provider: str, model: str, overrides: dict[str, Price] | None = None) -> Price | None:
    """The price of a model: from config first, then the bundled list. None when it isn't known."""
    for candidate in (model, _SNAPSHOT.sub("", model)):
        if overrides and candidate in overrides:
            return overrides[candidate]
        known = PRICES.get(provider, {}).get(candidate)
        if known is not None:
            return known
    return None


def cost(price: Price, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0) -> float:
    """USD for one call or a whole run. `input_tokens` includes the cached ones."""
    cached = min(cached_input_tokens, input_tokens)
    cached_rate = price.cached_input if price.cached_input is not None else price.input
    return ((input_tokens - cached) * price.input + cached * cached_rate + output_tokens * price.output) / 1e6


def next_call_cost(price: Price, prompt: str, expected_output_tokens: int, factor: float = 1.0) -> float:
    """What the next model call should cost: its prompt as written, and about as much output as the last one."""
    return cost(price, tokens(prompt, factor), expected_output_tokens)


def format_usd(amount: float | None) -> str:
    """$0.0031, $0.42, $12.30: enough digits to tell small runs apart."""
    if amount is None:
        return "—"
    if amount == 0:
        return "$0.00"
    if amount < 0.01:
        return f"${amount:.4f}"
    return f"${amount:.2f}"


# --- estimates ----------------------------------------------------------------------------------

# Calibrated against a real gpt-6-luna evaluation (2026-09-26): on cart.ts, one generation and one improvement
# round used about 5,100 input and 7,400 output tokens; these constants estimate about 5,900 and 6,900.
CHARS_PER_TOKEN = 4  # a rough rule for code and English; tokenizers differ by a few tens of percent
MUTANT_TOKENS = 45  # one described mutant in the improvement prompt: header plus original and mutated line
PROMPT_OVERHEAD_TOKENS = 300  # headings, the file paths and the task instructions around the code
MIN_TEST_FILE_TOKENS = 1_500
REASONING_FACTOR = 2.0  # output on top of the test file itself: reasoning, summary, structured fields


@dataclass(frozen=True)
class Estimate:
    """Expected and worst-case spend of a run, before it starts."""

    input_tokens: int
    output_tokens: int
    calls: int
    max_input_tokens: int
    max_output_tokens: int
    max_calls: int
    expected_usd: float | None
    max_usd: float | None


# Claude 4.7 and later count about 30% more tokens for the same text (Anthropic's pricing page).
_DENSER_TOKENIZER = re.compile(r"^claude-(?:fable|mythos|opus|sonnet)-(?:[5-9](?:-|$)|4-[7-9](?:-|$))")


def token_factor(model: str) -> float:
    return 1.3 if _DENSER_TOKENIZER.match(model) else 1.0


def tokens(text: str, factor: float = 1.0) -> int:
    return max(1, int(len(text) / CHARS_PER_TOKEN * factor))


def estimate(
    instructions: str,
    code: str,
    existing_tests: str | None,
    *,
    rounds: int,
    max_repairs: int,
    max_mutants: int,
    generate: bool,
    max_output_tokens: int,
    price: Price | None,
    factor: float = 1.0,
) -> Estimate:
    """What a run on one file should cost: one accepted proposal per stage, or every repair attempt at worst.

    `code` is the source with the local modules it imports, as the prompt shows them.
    """
    code_tokens = tokens(code, factor)
    fixed = tokens(instructions, factor) + code_tokens + PROMPT_OVERHEAD_TOKENS
    tests = tokens(existing_tests, factor) if existing_tests else 0
    # The test file the model writes: at least as long as the one it extends, and longer when it starts fresh.
    written = max(int(tests * 1.5), int(code_tokens * 1.5), MIN_TEST_FILE_TOKENS)
    calls: list[tuple[int, int]] = []
    if generate:
        calls.append((fixed + tests, written))
    improved = int(written * 1.3)
    for _ in range(rounds):
        calls.append((fixed + written + max_mutants * MUTANT_TOKENS, improved))
        written = improved
        improved = int(improved * 1.2)
    output_cap = max_output_tokens
    expected = [(i, min(int(o * REASONING_FACTOR), output_cap)) for i, o in calls]
    attempts = max_repairs + 1
    # A repair resends the task plus the rejected file and its problems, and the model writes it all again.
    worst = [(i + o, output_cap) for i, o in calls for _ in range(attempts)]
    total_in, total_out = sum(i for i, _ in expected), sum(o for _, o in expected)
    max_in, max_out = sum(i for i, _ in worst), sum(o for _, o in worst)
    return Estimate(
        input_tokens=total_in,
        output_tokens=total_out,
        calls=len(expected),
        max_input_tokens=max_in,
        max_output_tokens=max_out,
        max_calls=len(worst),
        expected_usd=cost(price, total_in, total_out) if price else None,
        max_usd=cost(price, max_in, max_out) if price else None,
    )
