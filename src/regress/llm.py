from __future__ import annotations

import math
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import httpx2 as httpx
import openai
import pydantic
from pydantic import BaseModel, Field

from regress.errors import LLMError
from regress.providers import api_key, missing_key

if TYPE_CHECKING:
    from regress.config import Settings

# A healthy stream sends an event every few seconds (gpt-6-luna: at most ~6s apart while thinking,
# continuously while writing), so two minutes of silence means the request has stalled.
DEFAULT_IDLE_TIMEOUT = 120.0
DEFAULT_MAX_DURATION = 1800.0
DEFAULT_MAX_OUTPUT_TOKENS = 32768
MAX_RESPONSE_CHARS = 4_000_000
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
    input_tokens: int = 0  # including cached_input_tokens
    output_tokens: int = 0
    cached_input_tokens: int = 0


class LLM(Protocol):
    model: str

    def propose(
        self,
        instructions: str,
        prompt: str,
        on_status: StatusCallback | None = None,
        on_warning: StatusCallback | None = None,
    ) -> Completion: ...


def require_api_key(provider_name: str = "openai", api_key_env: str | None = None) -> None:
    problem = missing_key(provider_name, api_key_env)
    if problem:
        raise LLMError(problem)


class _Retryable(Exception):
    """A failure worth one more try: a stalled or dropped connection, or an overloaded API."""


def _ignore(_: str) -> None:
    pass


class StreamingLLM:
    """Streams structured test files from a model provider.

    Streaming lets the caller show what the model is doing (waiting, thinking about <headline>,
    writing) and turns the timeout into an idle timeout: a request only times out when the API
    sends nothing for `idle_timeout` seconds. A total deadline and output budget also bound
    healthy streams and retries, so a run cannot consume resources indefinitely.
    Stalls are retried here rather than inside the SDK, so each retry is reported.
    Subclasses implement `_stream` and raise `_Retryable` for failures worth another try.
    """

    provider = "openai"
    label = "OpenAI"

    def __init__(
        self,
        model: str,
        reasoning_effort: str | None = None,
        client: object | None = None,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
        retries: int = 2,
        retry_delay: float = 3.0,
        max_duration: float = DEFAULT_MAX_DURATION,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        for name, value in (("idle_timeout", idle_timeout), ("max_duration", max_duration)):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and greater than zero")
        if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 10:
            raise ValueError("retries must be an integer between 0 and 10")
        if isinstance(retry_delay, bool) or not math.isfinite(retry_delay) or retry_delay < 0:
            raise ValueError("retry_delay must be finite and nonnegative")
        if (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or not 1 <= max_output_tokens <= 131072
        ):
            raise ValueError("max_output_tokens must be an integer between 1 and 131072")
        self._owns_client = client is None
        self._closed = False
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.idle_timeout = idle_timeout
        self.retries = retries
        self.retry_delay = retry_delay
        self.max_duration = max_duration
        self.max_output_tokens = max_output_tokens
        self.client = client if client is not None else self._client()

    def _client(self):
        """The SDK client, when none was injected. It must not retry by itself: `propose` does."""
        raise NotImplementedError

    def close(self) -> None:
        """Release an internally created client; injected clients remain their caller's responsibility."""
        if not self._closed:
            self._closed = True
            if self._owns_client:
                self.client.close()

    def __enter__(self) -> StreamingLLM:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def propose(
        self,
        instructions: str,
        prompt: str,
        on_status: StatusCallback | None = None,
        on_warning: StatusCallback | None = None,
    ) -> Completion:
        if self._closed:
            raise LLMError(f"The {self.label} client has already been closed.")
        on_status, on_warning = on_status or _ignore, on_warning or _ignore
        deadline = time.monotonic() + self.max_duration
        attempts = self.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                return self._stream(instructions, prompt, on_status, deadline)
            except _Retryable as error:
                if attempt == attempts:
                    raise LLMError(
                        f"{error} Gave up after {attempts} attempts. This is usually a temporary problem on "
                        f"{self.label}'s side: try again later or pick another model with --model. If the model "
                        "legitimately stays silent that long, raise llm_timeout in regress.toml."
                    ) from error
                on_warning(f"{error} Retrying (attempt {attempt + 1} of {attempts}).")
                on_status("retrying")
                time.sleep(min(self.retry_delay * attempt, self._remaining(deadline)))
        raise AssertionError("unreachable")

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LLMError(
                f"The model exceeded the total time limit of {_seconds(self.max_duration)} (llm_max_duration)."
            )
        return remaining

    def _stream(self, instructions: str, prompt: str, on_status: StatusCallback, deadline: float) -> Completion:
        raise NotImplementedError

    def _silence(self, phase: str) -> str:
        """Why a stalled stream was given up on, by what it was doing."""
        silence = _seconds(self.idle_timeout)
        return {
            "waiting": f"{self.label} did not start a response within {silence}.",
            "thinking": f"{self.model} stopped sending data while thinking ({silence} of silence).",
            "writing": f"{self.model} stopped sending data while writing ({silence} of silence).",
        }[phase]


class OpenAILLM(StreamingLLM):
    """The OpenAI Responses API, with reasoning summaries for the live status."""

    def __init__(self, model: str, reasoning_effort: str | None = None, client=None, **options) -> None:
        self.summaries = True  # reasoning summaries, dropped if the model or account can't produce them
        super().__init__(model, reasoning_effort, client, **options)

    def _client(self) -> openai.OpenAI:
        require_api_key("openai")
        return openai.OpenAI(timeout=httpx.Timeout(self.idle_timeout, connect=15.0), max_retries=0)

    def _stream(self, instructions: str, prompt: str, on_status: StatusCallback, deadline: float) -> Completion:
        remaining = self._remaining(deadline)
        reasoning = {"effort": self.reasoning_effort} if self.reasoning_effort else {}
        if self.summaries:
            reasoning["summary"] = "auto"
        extra = {"reasoning": reasoning} if reasoning else {}
        phase, status, summary, written = "waiting", "waiting for OpenAI to start", "", 0
        on_status(status)
        try:
            with self.client.responses.stream(
                model=self.model,
                instructions=instructions,
                input=prompt,
                text_format=TestFileProposal,
                store=False,
                max_output_tokens=self.max_output_tokens,
                timeout=httpx.Timeout(min(self.idle_timeout, remaining), connect=min(15.0, remaining)),
                **extra,
            ) as stream:
                for event in stream:
                    self._remaining(deadline)
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
                        summary = (summary + event.delta)[-8192:]
                        status = _thinking(summary)
                    elif kind == "response.output_text.delta":
                        written += len(event.delta)
                        if written > MAX_RESPONSE_CHARS:
                            raise LLMError("The model response exceeded the maximum supported size.")
                        status = f"writing ~{written // 4:,} tokens"
                    elif kind in ("response.failed", "response.incomplete", "error"):
                        raise LLMError(f"The model did not finish: {_failure_reason(event)}.")
                    on_status(status)  # on every event, so the caller can tell the stream is alive
                response = stream.get_final_response()
        except (openai.APITimeoutError, httpx.TimeoutException) as error:
            self._remaining(deadline)
            raise _Retryable(self._silence(phase)) from error
        except openai.RateLimitError as error:
            if error.code == "insufficient_quota" or "insufficient_quota" in str(error):
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
                return self._stream(instructions, prompt, on_status, deadline)
            raise LLMError(f"OpenAI rejected the request: {error.message}") from error
        except openai.APIStatusError as error:
            raise LLMError(f"OpenAI request failed ({error.status_code}): {error.message}") from error
        except (openai.OpenAIError, pydantic.ValidationError, RuntimeError, ValueError) as error:
            raise LLMError(f"OpenAI request failed: {error}") from error

        self._remaining(deadline)
        if response.status != "completed":
            raise LLMError(f"The model did not finish: {_failure_reason(response)} ({response.status}).")
        parsed = response.output_parsed
        if parsed is None:
            raise LLMError(f"The model returned no usable test file ({response.status}).")
        usage = response.usage
        details = getattr(usage, "input_tokens_details", None)
        return Completion(
            proposal=parsed,
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
            cached_input_tokens=(getattr(details, "cached_tokens", 0) or 0) if usage else 0,
        )


def _thinking(summary: str) -> str:
    """`thinking: <headline>` from a reasoning summary such as "**Checking array merges** ..."."""
    headlines = _HEADLINE.findall(summary)
    return f"thinking: {headlines[-1].strip()}" if headlines else "thinking"


def _failure_reason(event: object) -> str:
    response = getattr(event, "response", None) or event
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


class OpenAICompatibleLLM(StreamingLLM):
    """Any server with an OpenAI-style Chat Completions API: Ollama, vLLM, LM Studio, OpenRouter.

    Chat Completions with a JSON-schema `response_format` is the part of the OpenAI API these servers
    share most widely; the Responses API and reasoning summaries are not assumed.
    """

    provider = "openai-compatible"
    label = "The model server"

    def __init__(
        self,
        model: str,
        reasoning_effort: str | None = None,
        client=None,
        base_url: str | None = None,
        api_key_env: str | None = None,
        **options,
    ) -> None:
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL", "").strip() or None
        self.api_key_env = api_key_env
        if client is None and self.base_url is None:
            raise LLMError(
                'provider = "openai-compatible" needs the server\'s address: set base_url in regress.toml '
                "(for example http://localhost:11434/v1 for Ollama) or OPENAI_BASE_URL."
            )
        super().__init__(model, reasoning_effort, client, **options)

    def _client(self) -> openai.OpenAI:
        require_api_key(self.provider, self.api_key_env)
        return openai.OpenAI(
            base_url=self.base_url,
            api_key=api_key(self.provider, self.api_key_env) or "not-needed",  # local servers take any key
            timeout=httpx.Timeout(self.idle_timeout, connect=15.0),
            max_retries=0,
        )

    def _stream(self, instructions: str, prompt: str, on_status: StatusCallback, deadline: float) -> Completion:
        remaining = self._remaining(deadline)
        extra = {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}
        phase, written = "waiting", 0
        on_status(f"waiting for {self.model} to start")
        try:
            with self.client.chat.completions.stream(
                model=self.model,
                messages=[{"role": "system", "content": instructions}, {"role": "user", "content": prompt}],
                response_format=TestFileProposal,
                max_completion_tokens=self.max_output_tokens,
                stream_options={"include_usage": True},
                timeout=httpx.Timeout(min(self.idle_timeout, remaining), connect=min(15.0, remaining)),
                **extra,
            ) as stream:
                for event in stream:
                    self._remaining(deadline)
                    if event.type == "content.delta":
                        phase = "writing"
                        written += len(event.delta)
                        if written > MAX_RESPONSE_CHARS:
                            raise LLMError("The model response exceeded the maximum supported size.")
                        on_status(f"writing ~{written // 4:,} tokens")
                    elif event.type == "refusal.delta":
                        phase = "writing"
                    elif phase == "waiting":
                        phase = "thinking"  # chunks without content: reasoning models think first
                        on_status("thinking")
                    else:
                        on_status("thinking" if phase == "thinking" else f"writing ~{written // 4:,} tokens")
                completion = stream.get_final_completion()
        except (openai.APITimeoutError, httpx.TimeoutException) as error:
            self._remaining(deadline)
            raise _Retryable(self._silence(phase)) from error
        except openai.RateLimitError as error:
            raise _Retryable(f"The server is rate limiting requests ({error.message}).") from error
        except openai.InternalServerError as error:
            raise _Retryable(f"The server had an error ({error.status_code}).") from error
        except (openai.APIConnectionError, httpx.TransportError) as error:
            raise _Retryable(
                f"The connection to {self.base_url or 'the server'} failed ({type(error).__name__})."
            ) from error
        except openai.AuthenticationError as error:
            raise LLMError(f"The server rejected the API key: {error.message}") from error
        except openai.NotFoundError as error:
            raise LLMError(f"The server does not know model {self.model}: {error.message}") from error
        except openai.APIStatusError as error:
            raise LLMError(f"The server rejected the request ({error.status_code}): {error.message}") from error
        except (openai.OpenAIError, pydantic.ValidationError, RuntimeError, ValueError) as error:
            # Includes LengthFinishReasonError (output budget reached) and invalid JSON from the model.
            raise LLMError(f"The model's response was unusable: {error}") from error

        self._remaining(deadline)
        choice = completion.choices[0] if completion.choices else None
        message = choice.message if choice else None
        if message is not None and message.refusal:
            raise LLMError(f"The model declined: {message.refusal}")
        if message is None or message.parsed is None:
            raise LLMError("The model returned no usable test file.")
        usage = completion.usage
        details = getattr(usage, "prompt_tokens_details", None)
        return Completion(
            proposal=message.parsed,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            cached_input_tokens=(getattr(details, "cached_tokens", 0) or 0) if usage else 0,
        )


def create_llm(settings: Settings, model: str | None = None) -> StreamingLLM:
    """The client for the configured provider. `model` overrides the settings' model (a plain ID)."""
    model = model or settings.model
    if not model:
        raise LLMError(f"No model chosen for provider {settings.provider}. Pass --model or set model in regress.toml.")
    options = {
        "idle_timeout": settings.llm_timeout,
        "max_duration": settings.llm_max_duration,
        "max_output_tokens": settings.llm_max_output_tokens,
    }
    if settings.provider == "anthropic":
        from regress.llm_anthropic import AnthropicLLM

        return AnthropicLLM(
            model, settings.reasoning_effort, base_url=settings.base_url, api_key_env=settings.api_key_env, **options
        )
    if settings.provider == "openai-compatible":
        return OpenAICompatibleLLM(
            model, settings.reasoning_effort, base_url=settings.base_url, api_key_env=settings.api_key_env, **options
        )
    return OpenAILLM(model, settings.reasoning_effort, **options)
