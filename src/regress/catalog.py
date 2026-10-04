"""Which models can write tests: fetched from the provider's API, cached, with an offline fallback."""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import os
import re
import time
import typing
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

import openai

from regress.config import atomic_write_text, user_cache_dir
from regress.providers import PROVIDERS, api_key

CACHE_FILE = "models.json"
CACHE_TTL_SECONDS = 24 * 60 * 60
MAX_CACHE_BYTES = 5_000_000

# Text models that can return structured output through the Responses API.
_TEXT_MODEL = re.compile(r"^(?:ft:)?(?:gpt-|o\d)")
_LEGACY = re.compile(r"^gpt-(?:3\.5|4)(?:-|$)")
_NOT_FOR_TEXT = (
    "embedding", "tts", "whisper", "dall-e", "audio", "realtime", "transcribe", "search",
    "image", "moderation", "instruct", "computer-use", "deep-research", "sora", "live",
)  # fmt: skip
_SNAPSHOT = re.compile(r"-\d{4}-\d{2}-\d{2}$|^ft:|^claude-.*-\d{8}$")
# Current Claude models, newest first, for when the Models API can't be reached.
ANTHROPIC_MODELS = (
    "claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5", "claude-opus-5", "claude-sonnet-5",
    "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5",
)  # fmt: skip

Source = Literal["api", "cache", "sdk"]


@dataclass(frozen=True)
class ModelInfo:
    id: str
    created: int | None = None
    shutdown_date: str | None = None

    @property
    def created_date(self) -> str:
        return datetime.fromtimestamp(self.created, UTC).strftime("%Y-%m-%d") if self.created else ""

    @property
    def retiring(self) -> bool:
        return self.shutdown_date is not None


@dataclass
class Catalog:
    models: list[ModelInfo]  # text models worth listing, newest first
    source: Source
    fetched_at: float | None = None
    note: str | None = None
    all_ids: frozenset[str] = field(default_factory=frozenset)  # every model the key can use
    provider: str = "openai"

    def __post_init__(self) -> None:
        self.all_ids = frozenset(self.all_ids) | {m.id for m in self.models}

    @property
    def verified(self) -> bool:
        """Whether the list reflects what the API key can actually use."""
        return self.source in ("api", "cache")

    def latest(self, limit: int | None = 10, include_snapshots: bool = False) -> list[ModelInfo]:
        models = [m for m in self.models if include_snapshots or not is_snapshot(m.id)]
        return models if limit is None else models[:limit]

    def has(self, model_id: str) -> bool:
        """Typed names are checked against every model, so unlisted ones (e.g. fine-tunes) still work."""
        return model_id in self.all_ids

    def suggestions(self, model_id: str) -> list[str]:
        return difflib.get_close_matches(model_id, sorted(self.all_ids), n=3, cutoff=0.6)

    def describe(self) -> str:
        where = {"openai": "the OpenAI API", "anthropic": "the Anthropic API"}.get(self.provider, "the model server")
        if self.source == "api":
            return f"fetched from {where} just now"
        if self.source == "cache":
            return f"from {where}, cached {_ago(self.fetched_at)}"
        if self.provider == "anthropic":
            return "Regress's list of current Claude models"
        if self.provider == "openai-compatible":
            return "no list available"
        return f"bundled with the openai SDK {openai.__version__}"


def is_text_model(model_id: str, provider: str = "openai") -> bool:
    if provider == "anthropic":
        return model_id.startswith("claude-")
    if provider != "openai":
        return True  # a self-hosted server lists what it serves
    base = model_id.removeprefix("ft:")
    return bool(_TEXT_MODEL.match(model_id)) and not _LEGACY.match(base) and not any(x in base for x in _NOT_FOR_TEXT)


def is_snapshot(model_id: str) -> bool:
    return bool(_SNAPSHOT.search(model_id))


def usable(models: typing.Iterable[ModelInfo], today: date | None = None, provider: str = "openai") -> list[ModelInfo]:
    """Text models that are not shut down yet, newest first."""
    today = today or date.today()
    kept = [m for m in models if is_text_model(m.id, provider) and not _shut_down(m.shutdown_date, today)]
    return sorted(kept, key=lambda m: (m.created or 0, m.id), reverse=True)


def fetch_catalog(client, provider: str = "openai") -> Catalog:
    everything = [_model_info(m) for m in client.models.list()]
    return Catalog(
        usable(everything, provider=provider),
        "api",
        fetched_at=time.time(),
        all_ids=frozenset(m.id for m in everything),
        provider=provider,
    )


def _model_info(model: object) -> ModelInfo:
    created = getattr(model, "created", None)
    if created is None and isinstance(getattr(model, "created_at", None), datetime):  # Anthropic
        created = int(model.created_at.timestamp())
    return ModelInfo(model.id, created if type(created) is int else None, getattr(model, "shutdown_date", None))


def sdk_models(provider: str = "openai") -> list[ModelInfo]:
    """Model names known offline, newest first: the openai SDK's list, or Regress's list of Claude models."""
    if provider == "anthropic":
        return [ModelInfo(model_id) for model_id in ANTHROPIC_MODELS]
    if provider != "openai":
        return []
    from openai.types.shared.chat_model import ChatModel

    return [ModelInfo(model_id) for model_id in typing.get_args(ChatModel) if is_text_model(model_id)]


def load_catalog(
    refresh: bool = False,
    client=None,
    provider: str = "openai",
    base_url: str | None = None,
    api_key_env: str | None = None,
) -> Catalog:
    """Models available to the API key: cached for a day, refreshed from the API, or a built-in list."""
    scope = _cache_scope(client, provider, base_url, api_key_env)
    cached = _read_cache(scope, provider)
    if cached and not refresh and time.time() - (cached.fetched_at or 0) < CACHE_TTL_SECONDS:
        return cached
    owns_client = client is None
    offline = Catalog(sdk_models(provider), "sdk", provider=provider)
    key = api_key(provider, api_key_env)
    if owns_client and key is None and PROVIDERS[provider].key_required:
        offline.note = f"{PROVIDERS[provider].key_env} is not set"
        return cached or offline
    if owns_client and provider == "openai-compatible" and not (base_url or os.environ.get("OPENAI_BASE_URL")):
        offline.note = "base_url is not set"
        return cached or offline
    errors: tuple[type[Exception], ...] = (openai.OpenAIError,)
    try:
        if client is None:
            client = _list_client(provider, base_url, key)
        if provider == "anthropic":
            import anthropic

            errors = (anthropic.AnthropicError,)
        catalog = fetch_catalog(client, provider)
    except errors as error:
        where = {"openai": "the OpenAI API", "anthropic": "the Anthropic API"}.get(provider, "the model server")
        reason = f"could not reach {where} ({type(error).__name__})"
        if cached:
            cached.note = reason
            return cached
        offline.note = reason
        return offline
    finally:
        if owns_client and client is not None:
            client.close()
    _write_cache(catalog, scope, provider)
    return catalog


def _list_client(provider: str, base_url: str | None, key: str | None):
    if provider == "anthropic":
        import anthropic

        return anthropic.Anthropic(api_key=key, base_url=base_url, timeout=15, max_retries=1)
    if provider == "openai-compatible":
        return openai.OpenAI(base_url=base_url or None, api_key=key or "not-needed", timeout=15, max_retries=1)
    return openai.OpenAI(base_url=base_url or None, timeout=15, max_retries=1)


def _cache_scope(client, provider: str = "openai", base_url: str | None = None, api_key_env: str | None = None) -> str:
    """A cache from another provider, credential, project, or endpoint cannot verify this user's models."""
    if provider == "openai":
        default_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        values: tuple = (
            getattr(client, "api_key", os.environ.get("OPENAI_API_KEY", "")),
            str(getattr(client, "base_url", base_url or default_url)).rstrip("/"),
            getattr(client, "organization", os.environ.get("OPENAI_ORG_ID")),
            getattr(client, "project", os.environ.get("OPENAI_PROJECT_ID")),
        )
    else:
        values = (
            provider,
            getattr(client, "api_key", api_key(provider, api_key_env) or ""),
            str(getattr(client, "base_url", base_url or os.environ.get("OPENAI_BASE_URL", ""))).rstrip("/"),
        )
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def _cache_path(provider: str):
    return user_cache_dir() / (CACHE_FILE if provider == "openai" else f"models-{provider}.json")


def _read_cache(scope: str, provider: str = "openai") -> Catalog | None:
    path = _cache_path(provider)
    try:
        with path.open(encoding="utf-8") as handle:
            raw = handle.read(MAX_CACHE_BYTES + 1)
        if len(raw) > MAX_CACHE_BYTES:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("scope") != scope:
            return None
        fetched_at = data["fetched_at"]
        if (
            type(fetched_at) not in (int, float)
            or not math.isfinite(fetched_at)
            or not 0 <= fetched_at <= time.time() + 300
        ):
            return None
        entries, all_ids = data["models"], data["all_ids"]
        if (
            not isinstance(entries, list)
            or not isinstance(all_ids, list)
            or not all(_valid_id(value) for value in all_ids)
        ):
            return None
        models = []
        for entry in entries:
            if not isinstance(entry, dict) or not _valid_id(entry.get("id")):
                return None
            model = ModelInfo(**entry)
            if model.created is not None and (type(model.created) is not int or not 0 <= model.created <= 253402300799):
                return None
            if model.shutdown_date is not None:
                if not isinstance(model.shutdown_date, str):
                    return None
                date.fromisoformat(model.shutdown_date[:10])
            models.append(model)
        return Catalog(
            usable(models, provider=provider),
            "cache",
            fetched_at=fetched_at,
            all_ids=frozenset(all_ids),
            provider=provider,
        )
    except (OSError, ValueError, KeyError, TypeError, OverflowError, RecursionError):
        return None


def _valid_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 256
        and not any(char.isspace() or ord(char) < 32 for char in value)
    )


def _write_cache(catalog: Catalog, scope: str, provider: str = "openai") -> None:
    path = _cache_path(provider)
    try:
        payload = {
            "scope": scope,
            "fetched_at": catalog.fetched_at,
            "models": [asdict(m) for m in catalog.models],
            "all_ids": sorted(catalog.all_ids),
        }
        atomic_write_text(path, json.dumps(payload))
    except OSError:
        pass  # the cache is an optimization; failing to write it is not an error


def _shut_down(shutdown_date: str | None, today: date) -> bool:
    if not shutdown_date:
        return False
    try:
        return date.fromisoformat(shutdown_date[:10]) <= today
    except ValueError:
        return False


def _ago(timestamp: float | None) -> str:
    if timestamp is None:
        return "at an unknown time"
    minutes = int((time.time() - timestamp) // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    return f"{minutes // 60}h ago"
