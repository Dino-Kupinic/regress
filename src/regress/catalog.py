"""Which OpenAI models can write tests: fetched from the API, cached, with an offline fallback."""

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
_SNAPSHOT = re.compile(r"-\d{4}-\d{2}-\d{2}$|^ft:")

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
        if self.source == "api":
            return "fetched from the OpenAI API just now"
        if self.source == "cache":
            return f"from the OpenAI API, cached {_ago(self.fetched_at)}"
        return f"bundled with the openai SDK {openai.__version__}"


def is_text_model(model_id: str) -> bool:
    base = model_id.removeprefix("ft:")
    return bool(_TEXT_MODEL.match(model_id)) and not _LEGACY.match(base) and not any(x in base for x in _NOT_FOR_TEXT)


def is_snapshot(model_id: str) -> bool:
    return bool(_SNAPSHOT.search(model_id))


def usable(models: typing.Iterable[ModelInfo], today: date | None = None) -> list[ModelInfo]:
    """Text models that are not shut down yet, newest first."""
    today = today or date.today()
    kept = [m for m in models if is_text_model(m.id) and not _shut_down(m.shutdown_date, today)]
    return sorted(kept, key=lambda m: (m.created or 0, m.id), reverse=True)


def fetch_catalog(client: openai.OpenAI) -> Catalog:
    everything = [ModelInfo(m.id, m.created, getattr(m, "shutdown_date", None)) for m in client.models.list()]
    return Catalog(usable(everything), "api", fetched_at=time.time(), all_ids=frozenset(m.id for m in everything))


def sdk_models() -> list[ModelInfo]:
    """Model names known to the installed SDK, which lists them newest first."""
    from openai.types.shared.chat_model import ChatModel

    return [ModelInfo(model_id) for model_id in typing.get_args(ChatModel) if is_text_model(model_id)]


def load_catalog(refresh: bool = False, client: openai.OpenAI | None = None) -> Catalog:
    """Models available to the API key: cached for a day, refreshed from the API, or the SDK's list."""
    scope = _cache_scope(client)
    cached = _read_cache(scope)
    if cached and not refresh and time.time() - (cached.fetched_at or 0) < CACHE_TTL_SECONDS:
        return cached
    owns_client = client is None
    if owns_client and not os.environ.get("OPENAI_API_KEY", "").strip():
        return cached or Catalog(sdk_models(), "sdk", note="OPENAI_API_KEY is not set")
    try:
        if client is None:
            client = openai.OpenAI(timeout=15, max_retries=1)
        catalog = fetch_catalog(client)
    except openai.OpenAIError as error:
        reason = f"could not reach the OpenAI API ({type(error).__name__})"
        if cached:
            cached.note = reason
            return cached
        return Catalog(sdk_models(), "sdk", note=reason)
    finally:
        if owns_client and client is not None:
            client.close()
    _write_cache(catalog, scope)
    return catalog


def _cache_scope(client: openai.OpenAI | None) -> str:
    """A cache from another credential, project, or endpoint cannot verify this user's models."""
    values = (
        getattr(client, "api_key", os.environ.get("OPENAI_API_KEY", "")),
        str(getattr(client, "base_url", os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))).rstrip("/"),
        getattr(client, "organization", os.environ.get("OPENAI_ORG_ID")),
        getattr(client, "project", os.environ.get("OPENAI_PROJECT_ID")),
    )
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def _read_cache(scope: str) -> Catalog | None:
    path = user_cache_dir() / CACHE_FILE
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
        return Catalog(usable(models), "cache", fetched_at=fetched_at, all_ids=frozenset(all_ids))
    except (OSError, ValueError, KeyError, TypeError, OverflowError, RecursionError):
        return None


def _valid_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 256
        and not any(char.isspace() or ord(char) < 32 for char in value)
    )


def _write_cache(catalog: Catalog, scope: str) -> None:
    path = user_cache_dir() / CACHE_FILE
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
