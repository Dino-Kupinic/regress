from __future__ import annotations

import json
import os
import tempfile
import threading
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError

from regress.errors import ProjectError
from regress.pricing import Price
from regress.providers import PROVIDERS, ProviderName, split_model

CONFIG_FILE = "regress.toml"
DEFAULT_MODEL = PROVIDERS["openai"].default_model
# Settings the HTTP API may not change: together they decide where prompts and an API key are sent.
FILE_ONLY_SETTINGS = frozenset({"base_url", "api_key_env"})

CONFIG_TEMPLATE = """\
# Regress configuration for this project. Command-line flags override these values.

# provider = "openai"  # "openai", "anthropic", or "openai-compatible" (Ollama, vLLM, OpenRouter, ...)
# model = "gpt-6-luna"   # pin a model for everyone on this project (overrides your personal default)
# base_url = "http://localhost:11434/v1"  # the server's API, for provider = "openai-compatible"
# api_key_env = "OPENROUTER_API_KEY"      # environment variable holding that server's key, if it needs one
# reasoning_effort = "medium"  # for reasoning models (env: REGRESS_REASONING_EFFORT)
rounds = 1            # improvement rounds driven by surviving mutants
max_repairs = 2       # retries when a generated test file fails validation
max_mutants = 40      # undetected mutants sent to the model per round
runner = "auto"       # how to run Vitest and Stryker: "bun", "npx", or "auto"
# llm_timeout = 120   # seconds without any data from the model before Regress retries (up to 3 attempts)
# llm_max_duration = 1800  # total seconds allowed for one proposal, including retries
# llm_max_output_tokens = 32768  # output budget, including model reasoning
# fail_under = 80     # `regress check` fails below this combined mutation score (percent)
# max_cost = 0.50     # USD per run: Regress stops before a model call would go past it
# [prices."my-model"] # USD per million tokens, for models Regress has no price for (or newer prices)
# input = 1.0
# output = 4.0
# cached_input = 0.1
"""

USER_CONFIG_HEADER = "# Your personal Regress defaults. Manage them with `regress models`.\n"

ModelSource = Literal["built-in default", "user config", "regress.toml", "REGRESS_MODEL", "--model"]
_ENV_NAME = r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
_USER_CONFIG_LOCK = threading.RLock()


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True, validate_assignment=True)

    provider: ProviderName = "openai"
    # None only for a provider without a default model (openai-compatible) that nobody chose one for yet.
    model: str | None = Field(default=DEFAULT_MODEL, min_length=1, max_length=256, pattern=r"^[^\s\x00-\x1f\x7f]+$")
    base_url: str | None = Field(default=None, max_length=2048, pattern=r"^https?://[^\s\x00-\x1f\x7f]+$")
    api_key_env: str | None = Field(default=None, pattern=_ENV_NAME)
    ask_model: bool = True
    reasoning_effort: ReasoningEffort | None = None
    rounds: int = Field(default=1, ge=0, le=5)
    max_repairs: int = Field(default=2, ge=0, le=5)
    max_mutants: int = Field(default=40, ge=1, le=200)
    runner: Literal["auto", "bun", "npx"] = "auto"
    llm_timeout: int = Field(default=120, ge=30, le=3600)
    llm_max_duration: int = Field(default=1800, ge=30, le=14400)
    llm_max_output_tokens: int = Field(default=32768, ge=1024, le=131072)
    vitest_timeout: int = Field(default=300, ge=10, le=14400)
    stryker_timeout: int = Field(default=1800, ge=30, le=86400)
    fail_under: float | None = Field(default=None, ge=0, le=100)  # `regress check` gate, in percent
    max_cost: float | None = Field(default=None, gt=0, le=10_000)  # USD per run: no model call goes past it
    # USD per million tokens, by model ID, over the bundled list: {"gpt-6-luna" = {input = 0.1, output = 0.5}}
    prices: dict[str, Price] = Field(default_factory=dict, max_length=500)

    _model_source: ModelSource = PrivateAttr(default="built-in default")

    @property
    def model_source(self) -> ModelSource:
        return self._model_source

    @property
    def model_is_explicit(self) -> bool:
        """The model was chosen for this invocation (flag or environment), so there is nothing to ask."""
        return self._model_source in ("--model", "REGRESS_MODEL")


def user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "regress" / "config.toml"


def user_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "regress"


def load_settings(root: Path | None, **overrides: object) -> Settings:
    """Merge settings, later layers winning. None-valued overrides are ignored.

    built-in defaults < user config (~/.config/regress/config.toml) < project regress.toml
    < environment (REGRESS_PROVIDER, REGRESS_MODEL, REGRESS_REASONING_EFFORT) < command-line flags

    A model may name its provider (`anthropic:claude-opus-5-5`). A layer that switches the provider
    without naming a model drops the model of the layers below it, which belongs to another provider:
    the new provider's default model applies instead.
    """
    env = {
        key: os.environ[variable]
        for key, variable in (
            ("provider", "REGRESS_PROVIDER"),
            ("model", "REGRESS_MODEL"),
            ("reasoning_effort", "REGRESS_REASONING_EFFORT"),
        )
        if os.environ.get(variable)
    }
    layers: list[tuple[ModelSource, Path | None, dict[str, object]]] = [
        ("user config", user_config_path(), _read_toml(user_config_path())),
        ("regress.toml", root / CONFIG_FILE if root else None, _read_toml(root / CONFIG_FILE) if root else {}),
        ("REGRESS_MODEL", None, env),
        ("--model", None, {key: value for key, value in overrides.items() if value is not None}),
    ]
    data: dict[str, object] = {}
    source: ModelSource = "built-in default"
    for name, path, layer in layers:
        layer = _split_provider(layer)
        if path is not None:
            _validate(layer, str(path))
        if "provider" in layer and "model" not in layer and layer["provider"] != data.get("provider", "openai"):
            data.pop("model", None)
            source = "built-in default"
        data.update(layer)
        if "model" in layer:
            source = name
    if "model" not in data:
        provider = data.get("provider", "openai")
        data["model"] = PROVIDERS[provider].default_model if provider in PROVIDERS else None
    settings = _validate(data, f"{CONFIG_FILE}, user config, or flags")
    settings._model_source = source
    return settings


def _split_provider(layer: dict[str, object]) -> dict[str, object]:
    """`model = "anthropic:claude-opus-5-5"` sets the provider too."""
    model = layer.get("model")
    if not isinstance(model, str):
        return layer
    prefix, model_id = split_model(model.strip())
    if prefix is None:
        return layer
    return {**layer, "provider": prefix, "model": model_id}


def user_settings() -> dict[str, object]:
    """The keys set in the user config, as written there."""
    return _read_toml(user_config_path())


def save_user_settings(**values: object) -> Path:
    """Update keys in the user config, keeping the ones already there. A value of None removes the key."""
    path = user_config_path()
    with _USER_CONFIG_LOCK:
        data = {key: value for key, value in {**_read_toml(path), **values}.items() if value is not None}
        validated = _validate(data, str(path))
        lines = [f"{key} = {_toml_value(getattr(validated, key))}\n" for key in data]
        try:
            atomic_write_text(path, USER_CONFIG_HEADER + "".join(lines))
        except OSError as error:
            raise ProjectError(f"Could not save configuration to {path}: {error}") from error
    return path


def atomic_write_text(path: Path, content: str) -> None:
    """Replace a UTF-8 file only after the complete new contents have reached disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_toml(path: Path) -> dict[str, object]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ProjectError(f"Invalid {path}: {error}") from error


def _validate(data: dict[str, object], where: str) -> Settings:
    try:
        return Settings.model_validate(data)
    except ValidationError as error:
        details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors())
        raise ProjectError(f"Invalid configuration in {where}: {details}") from error


def _toml_value(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(exclude_none=True)
    if isinstance(value, dict):  # an inline table, e.g. prices = { "gpt-6-luna" = { input = 0.1 } }
        items = ", ".join(f"{json.dumps(str(k))} = {_toml_value(v)}" for k, v in value.items())
        return "{ " + items + " }" if items else "{}"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)  # preserve Unicode instead of JSON surrogate escapes
