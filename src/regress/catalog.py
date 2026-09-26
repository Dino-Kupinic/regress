"""Which OpenAI models can write tests: fetched from the API, cached, with an offline fallback."""

from __future__ import annotations

import difflib
import json
import os
import re
import time
import typing
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

import openai

from regress.config import user_cache_dir

CACHE_FILE = "models.json"
CACHE_TTL_SECONDS = 24 * 60 * 60

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
    cached = _read_cache()
    if cached and not refresh and time.time() - (cached.fetched_at or 0) < CACHE_TTL_SECONDS:
        return cached
    if client is None:
        if not os.environ.get("OPENAI_API_KEY"):
            return cached or Catalog(sdk_models(), "sdk", note="OPENAI_API_KEY is not set")
        client = openai.OpenAI(timeout=15, max_retries=1)
    try:
        catalog = fetch_catalog(client)
    except openai.OpenAIError as error:
        reason = f"could not reach the OpenAI API ({type(error).__name__})"
        if cached:
            cached.note = reason
            return cached
        return Catalog(sdk_models(), "sdk", note=reason)
    _write_cache(catalog)
    return catalog


def _read_cache() -> Catalog | None:
    path = user_cache_dir() / CACHE_FILE
    try:
        data = json.loads(path.read_text())
        models = [ModelInfo(**entry) for entry in data["models"]]
        return Catalog(models, "cache", fetched_at=float(data["fetched_at"]), all_ids=frozenset(data["all_ids"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cache(catalog: Catalog) -> None:
    path = user_cache_dir() / CACHE_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": catalog.fetched_at,
            "models": [asdict(m) for m in catalog.models],
            "all_ids": sorted(catalog.all_ids),
        }
        path.write_text(json.dumps(payload))
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
