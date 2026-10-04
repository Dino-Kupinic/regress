"""Model providers: settings, the Anthropic and OpenAI-compatible clients (through their real SDKs, over a mock
transport), their model catalogs, and the API surface. No request leaves the machine."""

from __future__ import annotations

import json
from collections.abc import Callable

import anthropic
import httpx2 as httpx
import openai
import pytest
from fastapi.testclient import TestClient

from regress.api import create_app
from regress.catalog import ANTHROPIC_MODELS, load_catalog
from regress.config import load_settings, save_user_settings
from regress.errors import LLMError
from regress.llm import OpenAICompatibleLLM, OpenAILLM, TestFileProposal, create_llm
from regress.llm_anthropic import FALLBACK_BETA, AnthropicLLM
from regress.providers import missing_key, qualified_model, split_model

PROPOSAL = TestFileProposal(summary="s", new_tests=["t"], equivalent_mutants=[], test_file="it('x', () => {});\n")


# --- settings ------------------------------------------------------------------------------------


def test_split_model_only_takes_known_provider_prefixes():
    assert split_model("anthropic:claude-opus-5-5") == ("anthropic", "claude-opus-5-5")
    assert split_model("openai-compatible:llama3.1:8b") == ("openai-compatible", "llama3.1:8b")
    assert split_model("llama3.1:8b") == (None, "llama3.1:8b")  # a colon of the model's own
    assert split_model("gpt-6-luna") == (None, "gpt-6-luna")
    assert qualified_model("openai", "gpt-6-luna") == "gpt-6-luna"
    assert qualified_model("anthropic", "claude-opus-5-5") == "anthropic:claude-opus-5-5"


def test_a_provider_switch_brings_its_own_default_model(tmp_path, monkeypatch):
    save_user_settings(model="gpt-6-sol")
    (tmp_path / "regress.toml").write_text('provider = "anthropic"\n')

    settings = load_settings(tmp_path)
    assert (settings.provider, settings.model, settings.model_source) == (
        "anthropic",
        "claude-opus-5-5",
        "built-in default",
    )

    flagged = load_settings(tmp_path, model="claude-sonnet-5-5")
    assert (flagged.provider, flagged.model, flagged.model_source) == ("anthropic", "claude-sonnet-5-5", "--model")

    prefixed = load_settings(tmp_path, model="openai:gpt-6-sol")
    assert (prefixed.provider, prefixed.model) == ("openai", "gpt-6-sol")

    monkeypatch.setenv("REGRESS_PROVIDER", "openai-compatible")
    compatible = load_settings(tmp_path)
    assert (compatible.provider, compatible.model) == ("openai-compatible", None)  # no default to fall back on


def test_a_remembered_provider_model_round_trips(tmp_path):
    save_user_settings(model="anthropic:claude-sonnet-5-5")
    settings = load_settings(tmp_path)
    assert (settings.provider, settings.model, settings.model_source) == (
        "anthropic",
        "claude-sonnet-5-5",
        "user config",
    )


def test_missing_keys_per_provider(monkeypatch):
    assert missing_key("openai") == "OPENAI_API_KEY is not set. Export it or add it to a .env file."
    assert "ANTHROPIC_API_KEY" in missing_key("anthropic")
    assert missing_key("openai-compatible") is None  # a local server needs none
    assert "OPENROUTER_API_KEY" in missing_key("openai-compatible", "OPENROUTER_API_KEY")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert missing_key("openai-compatible", "OPENROUTER_API_KEY") is None


def test_create_llm_picks_the_provider_client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert isinstance(create_llm(load_settings(tmp_path)), OpenAILLM)
    assert isinstance(create_llm(load_settings(tmp_path, provider="anthropic")), AnthropicLLM)
    compatible = load_settings(tmp_path, provider="openai-compatible", base_url="http://localhost:11434/v1")
    with pytest.raises(LLMError, match="No model chosen"):
        create_llm(compatible)
    llm = create_llm(compatible, "llama3.1:8b")
    assert isinstance(llm, OpenAICompatibleLLM) and str(llm.client.base_url).startswith("http://localhost:11434")
    with pytest.raises(LLMError, match="base_url"):
        create_llm(load_settings(tmp_path, provider="openai-compatible"), "llama3.1:8b")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        create_llm(load_settings(tmp_path, provider="anthropic"))


# --- Anthropic, through the real SDK ------------------------------------------------------------


def _sse(events: list[dict]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def claude_events(text: str, stop_reason: str = "end_turn", model: str = "claude-opus-5-5") -> list[dict]:
    return [
        {
            "type": "message_start",
            "message": {
                "id": "msg",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 100, "output_tokens": 0, "cache_read_input_tokens": 20},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "**Covering coupons**"},
        },
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": text[:10]}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": text[10:]}},
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 40},
        },
        {"type": "message_stop"},
    ]


Handler = Callable[[httpx.Request], httpx.Response]


class Recorder:
    """A mock transport answering with prepared responses in order, recording each request."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    @property
    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


def stream_response(body: bytes) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def claude(recorder: Recorder, model: str = "claude-opus-5-5", **options) -> AnthropicLLM:
    client = anthropic.Anthropic(
        api_key="test-key",
        base_url=options.pop("client_base_url", None),
        http_client=anthropic.DefaultHttpxClient(transport=httpx.MockTransport(recorder)),
        max_retries=0,
    )
    return AnthropicLLM(model, client=client, retry_delay=0, **options)


def test_claude_streams_a_structured_test_file_with_live_status():
    recorder = Recorder(stream_response(_sse(claude_events(PROPOSAL.model_dump_json()))))
    statuses: list[str] = []

    completion = claude(recorder, reasoning_effort="minimal").propose(
        "instructions", "prompt", on_status=statuses.append
    )

    assert completion.proposal == PROPOSAL
    assert (completion.input_tokens, completion.output_tokens) == (120, 40)  # cache reads count as input
    assert "thinking: Covering coupons" in statuses and any(s.startswith("writing ~") for s in statuses)
    [request], [body] = recorder.requests, recorder.bodies
    # Opus 5.5: adaptive thinking with summaries, effort mapped to Claude's scale, and refusal fallback.
    assert request.headers["anthropic-beta"] == FALLBACK_BETA and body["fallbacks"] == "default"
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"]["effort"] == "low"
    schema = body["output_config"]["format"]["schema"]
    assert schema["additionalProperties"] is False and set(schema["required"]) == set(TestFileProposal.model_fields)
    assert body["system"] == "instructions" and body["messages"] == [{"role": "user", "content": "prompt"}]


def test_claude_models_without_adaptive_thinking_or_fallback_get_a_plain_request():
    recorder = Recorder(stream_response(_sse(claude_events(PROPOSAL.model_dump_json(), model="claude-haiku-4-5"))))
    claude(recorder, model="claude-haiku-4-5").propose("i", "p")
    [request], [body] = recorder.requests, recorder.bodies
    assert "anthropic-beta" not in request.headers and "fallbacks" not in body
    assert "thinking" not in body and "effort" not in body["output_config"]


def test_claude_behind_a_custom_base_url_skips_the_fallback_beta():
    recorder = Recorder(stream_response(_sse(claude_events(PROPOSAL.model_dump_json()))))
    claude(recorder, base_url="https://proxy.example/anthropic").propose("i", "p")
    assert "fallbacks" not in recorder.bodies[0]


@pytest.mark.parametrize(
    ("stop_reason", "message"), [("refusal", "declined"), ("max_tokens", "ran out of output tokens")]
)
def test_claude_stop_reasons_without_a_test_file_are_errors(stop_reason, message):
    recorder = Recorder(stream_response(_sse(claude_events('{"summary": "', stop_reason))))
    with pytest.raises(LLMError, match=message):
        claude(recorder).propose("i", "p")


def test_claude_overload_is_retried_visibly_and_bad_keys_are_not():
    overloaded = httpx.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})
    recorder = Recorder(overloaded, stream_response(_sse(claude_events(PROPOSAL.model_dump_json()))))
    warnings: list[str] = []
    assert claude(recorder).propose("i", "p", on_warning=warnings.append).proposal == PROPOSAL
    assert len(recorder.requests) == 2 and "overloaded" in warnings[0] and "attempt 2 of 3" in warnings[0]

    unauthorized = httpx.Response(
        401, json={"type": "error", "error": {"type": "authentication_error", "message": "x"}}
    )
    recorder = Recorder(unauthorized)
    with pytest.raises(LLMError, match="rejected the API key"):
        claude(recorder).propose("i", "p")
    assert len(recorder.requests) == 1


# --- OpenAI-compatible servers, through the real SDK --------------------------------------------


def chat_chunks(text: str) -> bytes:
    def chunk(delta: dict, finish: str | None = None) -> dict:
        return {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "llama3.1:8b",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    events = [chunk({"role": "assistant", "content": ""}), chunk({"content": text[:10]}), chunk({"content": text[10:]})]
    events.append(chunk({}, "stop"))
    usage = {**chunk({}), "choices": [], "usage": {"prompt_tokens": 70, "completion_tokens": 30, "total_tokens": 100}}
    lines = [f"data: {json.dumps(e)}\n\n" for e in [*events, usage]]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


def compatible(recorder: Recorder, **options) -> OpenAICompatibleLLM:
    client = openai.OpenAI(
        api_key="k",
        base_url="http://localhost:11434/v1",
        http_client=openai.DefaultHttpxClient(transport=httpx.MockTransport(recorder)),
        max_retries=0,
    )
    return OpenAICompatibleLLM("llama3.1:8b", client=client, retry_delay=0, **options)


def test_compatible_server_streams_chat_completions_with_a_json_schema():
    recorder = Recorder(stream_response(chat_chunks(PROPOSAL.model_dump_json())))
    statuses: list[str] = []

    completion = compatible(recorder).propose("instructions", "prompt", on_status=statuses.append)

    assert completion.proposal == PROPOSAL and (completion.input_tokens, completion.output_tokens) == (70, 30)
    assert any(s.startswith("writing ~") for s in statuses)
    [request], [body] = recorder.requests, recorder.bodies
    assert request.url.path == "/v1/chat/completions"
    assert body["messages"][0] == {"role": "system", "content": "instructions"}
    assert body["response_format"]["type"] == "json_schema"
    assert body["stream_options"] == {"include_usage": True}


def test_compatible_server_reads_its_key_from_api_key_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-key")
    llm = OpenAICompatibleLLM("m", base_url="https://openrouter.example/api/v1", api_key_env="OPENROUTER_API_KEY")
    assert llm.client.api_key == "router-key"
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY"):
        OpenAICompatibleLLM("m", base_url="https://openrouter.example/api/v1", api_key_env="OPENROUTER_API_KEY")


def test_compatible_server_errors_are_classified():
    recorder = Recorder(httpx.Response(404, json={"error": {"message": "model not found"}}))
    with pytest.raises(LLMError, match="does not know model llama3.1:8b"):
        compatible(recorder).propose("i", "p")
    busy = httpx.Response(503, json={"error": {"message": "loading"}})
    recorder = Recorder(busy, stream_response(chat_chunks(PROPOSAL.model_dump_json())))
    assert compatible(recorder).propose("i", "p").proposal == PROPOSAL


# --- catalogs ------------------------------------------------------------------------------------


def test_anthropic_catalog_from_the_models_api_and_offline():
    page = {
        "data": [
            {"type": "model", "id": "claude-opus-5-5", "display_name": "Opus", "created_at": "2026-09-01T00:00:00Z"},
            {
                "type": "model",
                "id": "claude-haiku-4-5-20251001",
                "display_name": "H",
                "created_at": "2025-10-01T00:00:00Z",
            },
        ],
        "has_more": False,
        "first_id": "claude-opus-5-5",
        "last_id": "claude-haiku-4-5-20251001",
    }
    recorder = Recorder(httpx.Response(200, json=page))
    client = anthropic.Anthropic(
        api_key="k", http_client=anthropic.DefaultHttpxClient(transport=httpx.MockTransport(recorder))
    )
    catalog = load_catalog(client=client, provider="anthropic")
    assert catalog.source == "api" and catalog.describe() == "fetched from the Anthropic API just now"
    assert [m.id for m in catalog.latest()] == ["claude-opus-5-5"]  # the dated snapshot is hidden
    assert catalog.models[0].created_date == "2026-09-01" and catalog.has("claude-haiku-4-5-20251001")

    offline = load_catalog(provider="anthropic")
    assert offline.source == "sdk" and "ANTHROPIC_API_KEY" in offline.note
    assert [m.id for m in offline.models] == list(ANTHROPIC_MODELS)


def test_compatible_catalog_lists_whatever_the_server_serves():
    page = {"object": "list", "data": [{"id": "llama3.1:8b", "object": "model", "created": 5, "owned_by": "me"}]}
    recorder = Recorder(httpx.Response(200, json=page))
    client = openai.OpenAI(
        api_key="k",
        base_url="http://localhost:11434/v1",
        http_client=openai.DefaultHttpxClient(transport=httpx.MockTransport(recorder)),
    )
    assert [m.id for m in load_catalog(client=client, provider="openai-compatible").models] == ["llama3.1:8b"]
    offline = load_catalog(provider="openai-compatible")
    assert offline.models == [] and offline.note == "base_url is not set"


# --- the HTTP API --------------------------------------------------------------------------------


@pytest.fixture
def api(js_project) -> TestClient:
    with TestClient(create_app(js_project), base_url="http://127.0.0.1") as client:
        yield client


def test_api_never_sets_where_prompts_and_keys_go(api):
    for field, value in (("base_url", "https://attacker.example/v1"), ("api_key_env", "AWS_SECRET_ACCESS_KEY")):
        assert api.patch("/api/settings", json={field: value}).status_code == 422


def test_api_provider_switch_starts_from_the_new_default_and_reports_its_key(api):
    api.patch("/api/settings", json={"model": "gpt-6-sol"})
    settings = api.patch("/api/settings", json={"provider": "anthropic"}).json()
    assert (settings["effective"]["provider"], settings["effective"]["model"]) == ("anthropic", "claude-opus-5-5")

    project = api.get("/api/project").json()
    assert project["provider"] == "anthropic" and project["api_key_name"] == "ANTHROPIC_API_KEY"
    assert not project["api_key_set"] and any("ANTHROPIC_API_KEY" in p for p in project["problems"])

    models = api.get("/api/models").json()
    assert models["provider"] == "anthropic" and models["default_model"] == "claude-opus-5-5"
    assert api.get("/api/models", params={"provider": "openai"}).json()["default_model"] == "gpt-6-luna"


def test_api_flags_an_unconfigured_compatible_server(api):
    api.patch("/api/settings", json={"provider": "openai-compatible"})
    project = api.get("/api/project").json()
    assert project["api_key_name"] is None and project["api_key_set"]
    assert any("base_url" in p for p in project["problems"]) and any("No model" in p for p in project["problems"])
    assert api.get("/api/models").json()["default_model"] is None
