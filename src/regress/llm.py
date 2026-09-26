from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx2 as httpx
import openai
import pydantic
from pydantic import BaseModel, Field

from regress.errors import LLMError

DEFAULT_IDLE_TIMEOUT = 300.0
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

    Streaming lets the caller show what the model is doing (waiting, thinking, writing) and turns
    the timeout into an idle timeout: a request only times out when the API sends nothing for
    `idle_timeout` seconds, however long a healthy response takes overall. Retries are made here
    rather than inside the SDK so they are reported instead of silently multiplying the wait.
    """

    def __init__(
        self,
        model: str,
        reasoning_effort: str | None = None,
        client: openai.OpenAI | None = None,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
        retries: int = 1,
        retry_delay: float = 5.0,
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
                    raise LLMError(f"{error} Gave up after {attempts} attempts.") from error
                on_warning(f"{error} Retrying ({attempt + 1}/{attempts}).")
                on_status("retrying")
                time.sleep(self.retry_delay * attempt)
        raise AssertionError("unreachable")

    def _stream(self, instructions: str, prompt: str, on_status: StatusCallback) -> Completion:
        extra = {"reasoning": {"effort": self.reasoning_effort}} if self.reasoning_effort else {}
        on_status("waiting for a response")
        written, failure = 0, None
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
                        on_status("started")
                    elif kind == "response.output_item.added":
                        on_status("thinking" if event.item.type == "reasoning" else "writing")
                    elif kind == "response.output_text.delta":
                        written += len(event.delta)
                        on_status(f"writing ~{written // 4:,} tokens")
                    elif kind in ("response.failed", "response.incomplete", "error"):
                        failure = _failure_reason(event)
                if failure:
                    raise LLMError(f"The model did not finish: {failure}.")
                response = stream.get_final_response()
        except (openai.APITimeoutError, httpx.TimeoutException) as error:
            raise _Retryable(f"{self.model} sent nothing for {_seconds(self.idle_timeout)}.") from error
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
