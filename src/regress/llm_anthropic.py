"""Claude models through the Anthropic Messages API, streamed, with structured test-file output."""

from __future__ import annotations

import re

import anthropic
import httpx2 as httpx
import pydantic

from regress.errors import LLMError
from regress.llm import (
    MAX_RESPONSE_CHARS,
    Completion,
    StatusCallback,
    StreamingLLM,
    TestFileProposal,
    _Retryable,
    _thinking,
    require_api_key,
)
from regress.providers import api_key

# Models that take adaptive thinking and `effort`. Older ones (Haiku 4.5, the 4.5 and earlier families)
# get neither, and run with their defaults.
_ADAPTIVE = re.compile(r"^claude-(?:fable|mythos|opus|sonnet)-(?:[5-9](?:-|$)|4-[6-9](?:-|$))")
# Models with server-side refusal fallback ("default" form); requests to them go through the beta endpoint.
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Regress's effort vocabulary includes OpenAI's lower levels; Claude's lowest is "low".
_EFFORT = {"none": "low", "minimal": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh",
           "max": "max"}  # fmt: skip
# Sent as a JSON schema and parsed here, after the stop reason is known: the SDK's own `output_format`
# parses when the text block ends, so a refusal or a cut-off answer would surface as invalid JSON instead.
OUTPUT_FORMAT = {"type": "json_schema", "schema": anthropic.transform_schema(TestFileProposal)}
DEFAULT_EFFORT = "high"  # writing tests that kill mutants is coding work; Claude Opus 5.5 would default to medium


class AnthropicLLM(StreamingLLM):
    """Claude, with adaptive thinking (summarized, for the live status) and JSON-schema output."""

    provider = "anthropic"
    label = "Anthropic"

    def __init__(
        self,
        model: str,
        reasoning_effort: str | None = None,
        client=None,
        base_url: str | None = None,
        api_key_env: str | None = None,
        **options,
    ) -> None:
        self.base_url = base_url
        self.api_key_env = api_key_env
        super().__init__(model, reasoning_effort, client, **options)

    def _client(self) -> anthropic.Anthropic:
        require_api_key(self.provider, self.api_key_env)
        return anthropic.Anthropic(
            api_key=api_key(self.provider, self.api_key_env),
            base_url=self.base_url,
            timeout=httpx.Timeout(self.idle_timeout, connect=15.0),
            max_retries=0,
        )

    @property
    def adaptive(self) -> bool:
        return bool(_ADAPTIVE.match(self.model))

    @property
    def fallbacks(self) -> bool:
        """Re-run a declined request on Anthropic's recommended fallback model, server-side.

        Only on the Claude API itself: a custom base_url may be a proxy or platform without the feature.
        """
        return self.model in _FALLBACK_MODELS and self.base_url is None

    def request(self, instructions: str, prompt: str, timeout: httpx.Timeout) -> dict:
        """The keyword arguments for `messages.stream`, without the client."""
        params: dict = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "system": instructions,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"format": OUTPUT_FORMAT},
            "timeout": timeout,
        }
        if self.adaptive:
            params["thinking"] = {"type": "adaptive", "display": "summarized"}
            params["output_config"]["effort"] = _EFFORT.get(self.reasoning_effort or "", DEFAULT_EFFORT)
        if self.fallbacks:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def _stream(self, instructions: str, prompt: str, on_status: StatusCallback, deadline: float) -> Completion:
        remaining = self._remaining(deadline)
        timeout = httpx.Timeout(min(self.idle_timeout, remaining), connect=min(15.0, remaining))
        params = self.request(instructions, prompt, timeout)
        messages = self.client.beta.messages if "betas" in params else self.client.messages
        phase, status, summary, written = "waiting", "waiting for Anthropic to start", "", 0
        on_status(status)
        try:
            with messages.stream(**params) as stream:
                for event in stream:
                    self._remaining(deadline)
                    kind = event.type
                    if kind == "message_start":
                        phase, status = "thinking", "thinking"
                    elif kind == "content_block_start":
                        if event.content_block.type == "thinking":
                            phase, summary, status = "thinking", "", "thinking"
                        elif event.content_block.type == "text":
                            phase, status = "writing", "writing"
                    elif kind == "content_block_delta":
                        if event.delta.type == "thinking_delta":
                            summary = (summary + event.delta.thinking)[-8192:]
                            status = _thinking(summary)
                        elif event.delta.type == "text_delta":
                            written += len(event.delta.text)
                            if written > MAX_RESPONSE_CHARS:
                                raise LLMError("The model response exceeded the maximum supported size.")
                            status = f"writing ~{written // 4:,} tokens"
                    on_status(status)  # on every event, so the caller can tell the stream is alive
                message = stream.get_final_message()
        except (anthropic.APITimeoutError, httpx.TimeoutException) as error:
            self._remaining(deadline)
            raise _Retryable(self._silence(phase)) from error
        except anthropic.RateLimitError as error:
            raise _Retryable(f"Anthropic is rate limiting requests ({error.message}).") from error
        except (anthropic.OverloadedError, anthropic.ServiceUnavailableError, anthropic.InternalServerError) as error:
            raise _Retryable(f"Anthropic is overloaded or had a server error ({error.status_code}).") from error
        except (anthropic.APIConnectionError, httpx.TransportError) as error:
            raise _Retryable(f"The connection to Anthropic failed ({type(error).__name__}).") from error
        except anthropic.AuthenticationError as error:
            raise LLMError(f"Anthropic rejected the API key: {error.message}") from error
        except anthropic.PermissionDeniedError as error:
            raise LLMError(f"Anthropic denied access: {error.message}") from error
        except anthropic.NotFoundError as error:
            raise LLMError(f"Anthropic does not know model {self.model}: {error.message}") from error
        except anthropic.BadRequestError as error:
            raise LLMError(f"Anthropic rejected the request: {error.message}") from error
        except anthropic.APIStatusError as error:
            raise LLMError(f"Anthropic request failed ({error.status_code}): {error.message}") from error
        except (anthropic.AnthropicError, RuntimeError, ValueError) as error:
            raise LLMError(f"Anthropic request failed: {error}") from error

        self._remaining(deadline)
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None)
            raise LLMError(f"{self.model} declined to write these tests" + (f" ({category})." if category else "."))
        if message.stop_reason == "max_tokens":
            raise LLMError(
                f"The model ran out of output tokens ({self.max_output_tokens}) before finishing the test file. "
                "Raise llm_max_output_tokens in regress.toml."
            )
        text = "".join(block.text for block in message.content if block.type == "text")
        try:
            parsed = TestFileProposal.model_validate_json(text)
        except pydantic.ValidationError as error:
            raise LLMError(f"The model returned no usable test file ({message.stop_reason}): {error}") from error
        usage = message.usage
        read = usage.cache_read_input_tokens or 0
        written = usage.cache_creation_input_tokens or 0
        return Completion(
            proposal=parsed,
            input_tokens=usage.input_tokens + read + written,
            output_tokens=usage.output_tokens,
            cached_input_tokens=read,
        )
