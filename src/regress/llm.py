from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import openai
import pydantic
from pydantic import BaseModel, Field

from regress.errors import LLMError


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

    def propose(self, instructions: str, prompt: str) -> Completion: ...


def require_api_key() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise LLMError("OPENAI_API_KEY is not set. Export it or add it to a .env file.")


class OpenAILLM:
    def __init__(self, model: str, reasoning_effort: str | None = None, client: openai.OpenAI | None = None) -> None:
        if client is None:
            require_api_key()
            client = openai.OpenAI(timeout=600, max_retries=2)
        self.client = client
        self.model = model
        self.reasoning_effort = reasoning_effort

    def propose(self, instructions: str, prompt: str) -> Completion:
        extra = {"reasoning": {"effort": self.reasoning_effort}} if self.reasoning_effort else {}
        try:
            response = self.client.responses.parse(
                model=self.model,
                instructions=instructions,
                input=prompt,
                text_format=TestFileProposal,
                store=False,
                **extra,
            )
        except openai.AuthenticationError as error:
            raise LLMError(f"OpenAI rejected the API key: {error.message}") from error
        except openai.APIStatusError as error:
            raise LLMError(f"OpenAI request failed ({error.status_code}): {error.message}") from error
        except (openai.OpenAIError, pydantic.ValidationError) as error:
            raise LLMError(f"OpenAI request failed: {error}") from error

        parsed = response.output_parsed
        if parsed is None:
            reason = getattr(response.incomplete_details, "reason", None) or response.status
            raise LLMError(f"The model returned no usable test file ({reason}).")
        usage = response.usage
        return Completion(
            proposal=parsed,
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
        )
