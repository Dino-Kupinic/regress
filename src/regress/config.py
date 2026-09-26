from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError

from regress.errors import ProjectError

CONFIG_FILE = "regress.toml"
DEFAULT_MODEL = "gpt-6-luna"

CONFIG_TEMPLATE = """\
# Regress configuration for this project. Command-line flags override these values.

# model = "gpt-6-luna"   # pin a model for everyone on this project (overrides your personal default)
# reasoning_effort = "medium"  # for reasoning models (env: REGRESS_REASONING_EFFORT)
rounds = 1            # improvement rounds driven by surviving mutants
max_repairs = 2       # retries when a generated test file fails validation
max_mutants = 40      # undetected mutants sent to the model per round
runner = "auto"       # how to run Vitest and Stryker: "bun", "npx", or "auto"
# llm_timeout = 300   # seconds the model may send nothing before Regress retries once
"""

USER_CONFIG_HEADER = "# Your personal Regress defaults. Manage them with `regress models`.\n"

ModelSource = Literal["built-in default", "user config", "regress.toml", "REGRESS_MODEL", "--model"]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = DEFAULT_MODEL
    ask_model: bool = True
    reasoning_effort: str | None = None
    rounds: int = Field(default=1, ge=0, le=5)
    max_repairs: int = Field(default=2, ge=0, le=5)
    max_mutants: int = Field(default=40, ge=1, le=200)
    runner: Literal["auto", "bun", "npx"] = "auto"
    llm_timeout: int = Field(default=300, ge=30, le=3600)
    vitest_timeout: int = Field(default=300, ge=10)
    stryker_timeout: int = Field(default=1800, ge=30)

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
    < environment (REGRESS_MODEL, REGRESS_REASONING_EFFORT) < command-line flags
    """
    env = {
        key: os.environ[variable]
        for key, variable in (("model", "REGRESS_MODEL"), ("reasoning_effort", "REGRESS_REASONING_EFFORT"))
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
        if path is not None:
            _validate(layer, str(path))
        data.update(layer)
        if "model" in layer:
            source = name
    settings = _validate(data, f"{CONFIG_FILE}, user config, or flags")
    settings._model_source = source
    return settings


def user_settings() -> dict[str, object]:
    """The keys set in the user config, as written there."""
    return _read_toml(user_config_path())


def save_user_settings(**values: object) -> Path:
    """Update keys in the user config, keeping the ones already there."""
    path = user_config_path()
    data = {**_read_toml(path), **values}
    _validate(data, str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key} = {_toml_value(value)}\n" for key, value in data.items() if value is not None]
    path.write_text(USER_CONFIG_HEADER + "".join(lines))
    return path


def _read_toml(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as error:
        raise ProjectError(f"Invalid {path}: {error}") from error


def _validate(data: dict[str, object], where: str) -> Settings:
    try:
        return Settings.model_validate(data)
    except ValidationError as error:
        details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors())
        raise ProjectError(f"Invalid configuration in {where}: {details}") from error


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(str(value))  # a JSON string is a valid TOML basic string
