"""The model providers Regress can use, where their keys come from, and the `provider:model` form."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, get_args

ProviderName = Literal["openai", "anthropic", "openai-compatible"]
PROVIDER_NAMES: tuple[str, ...] = get_args(ProviderName)


@dataclass(frozen=True)
class Provider:
    name: ProviderName
    label: str  # for messages: "OpenAI did not start a response"
    key_env: str | None  # where the API key is read from, unless `api_key_env` says otherwise
    default_model: str | None
    key_required: bool = True


PROVIDERS: dict[str, Provider] = {
    "openai": Provider("openai", "OpenAI", "OPENAI_API_KEY", "gpt-6-luna"),
    "anthropic": Provider("anthropic", "Anthropic", "ANTHROPIC_API_KEY", "claude-opus-5-5"),
    # A local server (Ollama, vLLM) needs no key; a hosted one (OpenRouter) reads it from `api_key_env`.
    "openai-compatible": Provider("openai-compatible", "The model server", None, None, key_required=False),
}


def provider(name: str) -> Provider:
    try:
        return PROVIDERS[name]
    except KeyError:
        raise ValueError(f"Unknown provider {name!r}. Expected one of: {', '.join(PROVIDER_NAMES)}.") from None


def split_model(spec: str) -> tuple[str | None, str]:
    """`anthropic:claude-opus-5-5` -> ("anthropic", "claude-opus-5-5"); a plain ID has no provider.

    Only a known provider name counts as a prefix, so IDs with colons of their own (`llama3.1:8b`) stay whole.
    """
    prefix, colon, rest = spec.partition(":")
    if colon and prefix in PROVIDERS and rest:
        return prefix, rest
    return None, spec


def key_env(name: str, api_key_env: str | None = None) -> str | None:
    """The environment variable holding the provider's API key."""
    return api_key_env or provider(name).key_env


def api_key(name: str, api_key_env: str | None = None) -> str | None:
    env = key_env(name, api_key_env)
    value = os.environ.get(env, "").strip() if env else ""
    return value or None


def missing_key(name: str, api_key_env: str | None = None) -> str | None:
    """Why runs can't call the provider yet, or None when its key is set (or it needs none)."""
    env = key_env(name, api_key_env)
    if env is None or (api_key(name, api_key_env) is not None):
        return None
    if not provider(name).key_required and api_key_env is None:
        return None
    return f"{env} is not set. Export it or add it to a .env file."


def qualified_model(provider_name: str, model: str) -> str:
    """The form to save a model in: plain for OpenAI (as before providers existed), else `provider:model`."""
    return model if provider_name == "openai" else f"{provider_name}:{model}"
