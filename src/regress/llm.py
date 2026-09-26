from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx2 as httpx
import openai
import pydantic
from pydantic import BaseModel, Field

from regress.errors import LLMError

# A healthy stream sends an event every few seconds (gpt-6-luna: at most ~6s apart while thinking,
# continuously while writing), so two minutes of silence means the request has stalled.
DEFAULT_IDLE_TIMEOUT = 120.0
_HEADLINE = re.compile(r"\*\*(.+?)\*\*")
StatusCallback = Callable[[str], None]


class TestFileProposal(BaseModel):
    __test__ = False  # not a pytest test class

    summary: str = Field(description="One or two sentences on which behaviors the new tests pin down.")
    new_tests: list[str] = Field(description="Names of the tests you added.")
    equivalent_mutants: list[str] = Field(
        description=(
            "IDs of listed mutants you believe are equivalent: no test can distinguish them from the "
            "original code. Empty when no mutants were listed."
        )
    )
    test_file: str = Field(description="The complete contents of the test file, including every existing test.")


@dataclass
class Completion:
    proposal: TestFileProposal
    input_tokens: int = 0
    output_tokens: int = 0


class LLM(Protocol):
    model: str

    def propose(
        self,
        instructions: str,
        prompt: str,
        on_status: StatusCallback | None = None,
        on_warning: StatusCallback | None = None,
    ) -> Completion: ...


def require_api_key() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise LLMError("OPENAI_API_KEY is not set. Export it or add it to a .env file.")


class _Retryable(Exception):
    """A failure worth one more try: a stalled or dropped connection, or an overloaded API."""


def _ignore(_: str) -> None:
    pass


class OpenAILLM:
    """Streams structured test files from the OpenAI Responses API.

    Streaming lets the caller show what the model is doing (waiting, thinking about <headline>,
    writing) and turns the timeout into an idle timeout: a request only times out when the API
    sends nothing for `idle_timeout` seconds, however long a healthy response takes overall.
    Stalls are retried here rather than inside the SDK, so each retry is reported.
    """

    def __init__(
        self,
        model: str,
        reasoning_effort: str | None = None,
        client: openai.OpenAI | None = None,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
        retries: int = 2,
        retry_delay: float = 3.0,
    ) -> None:
        if client is None:
            require_api_key()
            client = openai.OpenAI(timeout=httpx.Timeout(idle_timeout, connect=15.0), max_retries=0)
        self.client = client
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.idle_timeout = idle_timeout
        self.retries = retries
        self.retry_delay = retry_delay
        self.summaries = True  # reasoning summaries, dropped if the model or account can't produce them

    def propose(
        self,
        instructions: str,
        prompt: str,
        on_status: StatusCallback | None = None,
        on_warning: StatusCallback | None = None,
    ) -> Completion:
        on_status, on_warning = on_status or _ignore, on_warning or _ignore
        attempts = self.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                return self._stream(instructions, prompt, on_status)
            except _Retryable as error:
                if attempt == attempts:
                    raise LLMError(
                        f"{error} Gave up after {attempts} attempts. This is usually a temporary problem on "
                        "OpenAI's side: try again later or pick another model with --model. If the model "
                        "legitimately stays silent that long, raise llm_timeout in regress.toml."
                    ) from error
                on_warning(f"{error} Retrying (attempt {attempt + 1} of {attempts}).")
                on_status("retrying")
                time.sleep(self.retry_delay * attempt)
        raise AssertionError("unreachable")

    def _stream(self, instructions: str, prompt: str, on_status: StatusCallback) -> Completion:
        reasoning = {"effort": self.reasoning_effort} if self.reasoning_effort else {}
        if self.summaries:
            reasoning["summary"] = "auto"
        extra = {"reasoning": reasoning} if reasoning else {}
        phase, status, summary, written, failure = "waiting", "waiting for OpenAI to start", "", 0, None
        on_status(status)
        try:
            with self.client.responses.stream(
                model=self.model,
                instructions=instructions,
                input=prompt,
                text_format=TestFileProposal,
                store=False,
                **extra,
            ) as stream:
                for event in stream:
                    kind = event.type
                    if kind == "response.created":
                        phase, status = "thinking", "thinking"
                    elif kind == "response.output_item.added":
                        if event.item.type == "reasoning":
                            phase, status = "thinking", _thinking(summary)
                        else:
                            phase, status = "writing", "writing"
                    elif kind == "response.reasoning_summary_part.added":
                        summary = ""
                    elif kind == "response.reasoning_summary_text.delta":
                        summary += event.delta
                        status = _thinking(summary)
                    elif kind == "response.output_text.delta":
                        written += len(event.delta)
                        status = f"writing ~{written // 4:,} tokens"
                    elif kind in ("response.failed", "response.incomplete", "error"):
                        failure = _failure_reason(event)
                    on_status(status)  # on every event, so the caller can tell the stream is alive
                if failure:
                    raise LLMError(f"The model did not finish: {failure}.")
                response = stream.get_final_response()
        except (openai.APITimeoutError, httpx.TimeoutException) as error:
            silence = _seconds(self.idle_timeout)
            what = {
                "waiting": f"OpenAI did not start a response within {silence}.",
                "thinking": f"{self.model} stopped sending data while thinking ({silence} of silence).",
                "writing": f"{self.model} stopped sending data while writing ({silence} of silence).",
            }[phase]
            raise _Retryable(what) from error
        except openai.RateLimitError as error:
            if "insufficient_quota" in str(error):
                raise LLMError(f"OpenAI quota exhausted: {error.message}") from error
            raise _Retryable(f"OpenAI is rate limiting requests ({error.message}).") from error
        except openai.InternalServerError as error:
            raise _Retryable(f"OpenAI had a server error ({error.status_code}).") from error
        except (openai.APIConnectionError, httpx.TransportError) as error:
            raise _Retryable(f"The connection to OpenAI failed ({type(error).__name__}).") from error
        except openai.AuthenticationError as error:
            raise LLMError(f"OpenAI rejected the API key: {error.message}") from error
        except openai.BadRequestError as error:
            if self.summaries and ("summar" in error.message.lower() or "reasoning" in error.message.lower()):
                self.summaries = False  # e.g. not a reasoning model, or the org can't get summaries
                return self._stream(instructions, prompt, on_status)
            raise LLMError(f"OpenAI rejected the request: {error.message}") from error
        except openai.APIStatusError as error:
            raise LLMError(f"OpenAI request failed ({error.status_code}): {error.message}") from error
        except (openai.OpenAIError, pydantic.ValidationError, RuntimeError) as error:
            raise LLMError(f"OpenAI request failed: {error}") from error

        parsed = response.output_parsed
        if parsed is None:
            raise LLMError(f"The model returned no usable test file ({response.status}).")
        usage = response.usage
        return Completion(
            proposal=parsed,
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
        )


def _thinking(summary: str) -> str:
    """`thinking: <headline>` from a reasoning summary such as "**Checking array merges** ..."."""
    headlines = _HEADLINE.findall(summary)
    return f"thinking: {headlines[-1].strip()}" if headlines else "thinking"


def _failure_reason(event: object) -> str:
    response = getattr(event, "response", None)
    error = getattr(response, "error", None) or event
    details = getattr(response, "incomplete_details", None)
    return (
        getattr(details, "reason", None)
        or getattr(error, "message", None)
        or getattr(error, "code", None)
        or getattr(event, "type", "unknown")
    )


def _seconds(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m {seconds % 60:02d}s"
