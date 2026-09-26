"""The streaming OpenAI client: live status, idle timeouts, visible retries."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx2 as httpx
import openai
import pytest

from regress.errors import LLMError
from regress.llm import OpenAILLM, TestFileProposal, require_api_key

PROPOSAL = TestFileProposal(summary="s", new_tests=["t"], equivalent_mutants=[], test_file="it('x', () => {});\n")


# --- a fake stream, to test the event handling in isolation ------------------------------------


class FakeStream:
    def __init__(self, events, final=None, error: Exception | None = None):
        self.events, self.final, self.error = events, final, error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        yield from self.events
        if self.error:
            raise self.error

    def get_final_response(self):
        return self.final


def completed(parsed=PROPOSAL):
    return SimpleNamespace(
        output_parsed=parsed, status="completed", usage=SimpleNamespace(input_tokens=12, output_tokens=34)
    )


def healthy_events(text: str = '{"summary": "a longer piece of json"}'):
    return [
        SimpleNamespace(type="response.created"),
        SimpleNamespace(type="response.output_item.added", item=SimpleNamespace(type="reasoning")),
        SimpleNamespace(type="response.output_item.added", item=SimpleNamespace(type="message")),
        SimpleNamespace(type="response.output_text.delta", delta=text),
        SimpleNamespace(type="response.completed"),
    ]


class FakeResponses:
    def __init__(self, *streams):
        self.streams = list(streams)
        self.calls: list[dict] = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        item = self.streams.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def llm_with(*streams, **options):
    responses = FakeResponses(*streams)
    llm = OpenAILLM("gpt-test", client=SimpleNamespace(responses=responses), retry_delay=0, **options)
    return llm, responses


def test_streams_structured_output_without_storage_and_reports_progress():
    llm, responses = llm_with(FakeStream(healthy_events("x" * 4000), completed()))
    llm.reasoning_effort = "high"
    statuses: list[str] = []
    completion = llm.propose("INSTRUCTIONS", "PROMPT", on_status=statuses.append)

    call = responses.calls[0]
    assert call["text_format"] is TestFileProposal
    assert call["store"] is False
    assert call["reasoning"] == {"effort": "high", "summary": "auto"}
    assert (call["model"], call["instructions"], call["input"]) == ("gpt-test", "INSTRUCTIONS", "PROMPT")
    assert statuses == [
        "waiting for OpenAI to start",
        "thinking",
        "thinking",
        "writing",
        "writing ~1,000 tokens",
        "writing ~1,000 tokens",  # every event reports, so the caller knows the stream is alive
    ]
    assert (completion.proposal, completion.input_tokens, completion.output_tokens) == (PROPOSAL, 12, 34)


def test_a_stalled_stream_is_retried_with_a_warning_naming_the_phase():
    stalled = FakeStream(healthy_events()[:2], error=httpx.ReadTimeout("idle"))
    llm, responses = llm_with(stalled, FakeStream(healthy_events(), completed()), idle_timeout=120)
    warnings: list[str] = []
    completion = llm.propose("i", "p", on_warning=warnings.append)

    assert completion.proposal == PROPOSAL
    assert warnings == ["gpt-test stopped sending data while thinking (2m 00s of silence). Retrying (attempt 2 of 3)."]
    assert len(responses.calls) == 2


def test_gives_up_after_three_attempts_with_advice():
    timeout = openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    llm, responses = llm_with(timeout, timeout, timeout, idle_timeout=120)
    warnings: list[str] = []
    with pytest.raises(LLMError) as error:
        llm.propose("i", "p", on_warning=warnings.append)

    message = str(error.value)
    assert message.startswith("OpenAI did not start a response within 2m 00s. Gave up after 3 attempts.")
    assert "try again later" in message and "llm_timeout" in message
    assert len(warnings) == 2 and len(responses.calls) == 3


def test_shows_the_reasoning_summary_headline_while_thinking():
    events = [
        SimpleNamespace(type="response.created"),
        SimpleNamespace(type="response.output_item.added", item=SimpleNamespace(type="reasoning")),
        SimpleNamespace(type="response.reasoning_summary_part.added"),
        SimpleNamespace(type="response.reasoning_summary_text.delta", delta="**Checking array"),
        SimpleNamespace(type="response.reasoning_summary_text.delta", delta=" merges**\n\nI need to see how"),
        SimpleNamespace(type="response.reasoning_summary_part.added"),
        SimpleNamespace(type="response.reasoning_summary_text.delta", delta="**Planning edge cases**"),
    ]
    llm, _ = llm_with(FakeStream(events, completed()))
    statuses: list[str] = []
    llm.propose("i", "p", on_status=statuses.append)
    assert "thinking: Checking array merges" in statuses
    assert statuses[-1] == "thinking: Planning edge cases"


def test_falls_back_when_reasoning_summaries_are_not_supported():
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    unsupported = openai.BadRequestError(
        "Unsupported parameter: 'reasoning.summary' is not supported with this model.",
        response=httpx.Response(400, request=request),
        body=None,
    )
    llm, responses = llm_with(unsupported, FakeStream(healthy_events(), completed()))
    assert llm.propose("i", "p").proposal == PROPOSAL
    assert responses.calls[0]["reasoning"] == {"summary": "auto"}
    assert "reasoning" not in responses.calls[1]
    assert llm.summaries is False


def test_other_bad_requests_are_not_retried():
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    bad = openai.BadRequestError(
        "Invalid schema for response_format", response=httpx.Response(400, request=request), body=None
    )
    llm, responses = llm_with(bad)
    with pytest.raises(LLMError, match="rejected the request: Invalid schema"):
        llm.propose("i", "p")
    assert len(responses.calls) == 1


def test_failed_and_incomplete_responses_explain_why():
    failed = SimpleNamespace(
        type="response.failed",
        response=SimpleNamespace(error=SimpleNamespace(message="server overloaded"), incomplete_details=None),
    )
    llm, _ = llm_with(FakeStream([failed]))
    with pytest.raises(LLMError, match="server overloaded"):
        llm.propose("i", "p")

    incomplete = SimpleNamespace(
        type="response.incomplete",
        response=SimpleNamespace(error=None, incomplete_details=SimpleNamespace(reason="max_output_tokens")),
    )
    llm, _ = llm_with(FakeStream([incomplete]))
    with pytest.raises(LLMError, match="max_output_tokens"):
        llm.propose("i", "p")


def test_exhausted_quota_is_not_retried():
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    quota = openai.RateLimitError(
        "You exceeded your current quota (insufficient_quota)",
        response=httpx.Response(429, request=request),
        body={"code": "insufficient_quota"},
    )
    llm, responses = llm_with(quota, FakeStream(healthy_events(), completed()))
    with pytest.raises(LLMError, match="quota exhausted"):
        llm.propose("i", "p")
    assert len(responses.calls) == 1


def test_empty_output_is_an_llm_error():
    llm, _ = llm_with(FakeStream(healthy_events(), completed(parsed=None)))
    with pytest.raises(LLMError, match="no usable test file"):
        llm.propose("i", "p")


def test_missing_api_key_is_reported():
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        require_api_key()
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        OpenAILLM("gpt-test")


# --- the real SDK over a mocked HTTP transport ---------------------------------------------------


def sse(*events: dict) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


def response_events(proposal: TestFileProposal) -> list[dict]:
    text = proposal.model_dump_json()
    half = len(text) // 2
    base = {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "model": "gpt-test",
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
    }
    reasoning = {"id": "rs_1", "type": "reasoning", "summary": []}
    message = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }
    usage = {
        "input_tokens": 10,
        "output_tokens": 20,
        "total_tokens": 30,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 5},
    }
    where = {"item_id": "msg_1", "output_index": 1, "content_index": 0}
    return [
        {"type": "response.created", "sequence_number": 0, "response": {**base, "status": "in_progress", "output": []}},
        {"type": "response.output_item.added", "sequence_number": 1, "output_index": 0, "item": reasoning},
        {"type": "response.output_item.done", "sequence_number": 2, "output_index": 0, "item": reasoning},
        {
            "type": "response.output_item.added",
            "sequence_number": 3,
            "output_index": 1,
            "item": {**message, "status": "in_progress", "content": []},
        },
        {
            "type": "response.content_part.added",
            "sequence_number": 4,
            **where,
            "part": {"type": "output_text", "text": "", "annotations": []},
        },
        {"type": "response.output_text.delta", "sequence_number": 5, **where, "delta": text[:half], "logprobs": []},
        {"type": "response.output_text.delta", "sequence_number": 6, **where, "delta": text[half:], "logprobs": []},
        {"type": "response.output_text.done", "sequence_number": 7, **where, "text": text, "logprobs": []},
        {"type": "response.output_item.done", "sequence_number": 8, "output_index": 1, "item": message},
        {
            "type": "response.completed",
            "sequence_number": 9,
            "response": {**base, "status": "completed", "output": [reasoning, message], "usage": usage},
        },
    ]


class StallingStream(httpx.SyncByteStream):
    """Sends a few events, then goes silent until the read times out."""

    def __init__(self, body: bytes):
        self.body = body

    def __iter__(self):
        yield self.body
        raise httpx.ReadTimeout("no data")


def real_sdk_llm(*replies) -> tuple[OpenAILLM, list[httpx.Request]]:
    requests: list[httpx.Request] = []
    replies_left = list(replies)

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        reply = replies_left.pop(0)
        if isinstance(reply, Exception):
            raise reply
        headers = {"content-type": "text/event-stream"}
        if isinstance(reply, httpx.SyncByteStream):
            return httpx.Response(200, headers=headers, stream=reply)
        return httpx.Response(200, headers=headers, content=reply)

    client = openai.OpenAI(
        api_key="sk-test",
        base_url="https://api.test/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    return OpenAILLM("gpt-test", client=client, retry_delay=0), requests


def test_real_sdk_parses_a_streamed_response():
    llm, requests = real_sdk_llm(sse(*response_events(PROPOSAL)))
    statuses: list[str] = []
    completion = llm.propose("i", "p", on_status=statuses.append)

    assert completion.proposal == PROPOSAL
    assert (completion.input_tokens, completion.output_tokens) == (10, 20)
    assert statuses[0] == "waiting for OpenAI to start"
    assert statuses.index("thinking") < statuses.index("writing")
    assert statuses[-1].startswith("writing ~")
    body = json.loads(requests[0].content)
    assert body["stream"] is True and body["store"] is False
    assert body["reasoning"] == {"summary": "auto"}
    assert body["text"]["format"]["type"] == "json_schema"


def test_real_sdk_stalls_are_retried():
    events = response_events(PROPOSAL)
    llm, requests = real_sdk_llm(
        httpx.ReadTimeout("nothing before headers"),
        StallingStream(sse(*events[:3])),
        sse(*events),
    )
    warnings: list[str] = []
    completion = llm.propose("i", "p", on_warning=warnings.append)

    assert completion.proposal == PROPOSAL
    assert len(requests) == 3
    assert warnings[0].startswith("OpenAI did not start a response within")
    assert warnings[1].startswith("gpt-test stopped sending data while thinking")
