from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from regress.errors import ProjectError

CONFIG_FILE = "regress.toml"
DEFAULT_MODEL = "gpt-5.5"

CONFIG_TEMPLATE = f"""\
# Regress configuration. Command-line flags override these values.

model = "{DEFAULT_MODEL}"   # OpenAI model that writes the tests (env: REGRESS_MODEL)
# reasoning_effort = "medium"  # for reasoning models (env: REGRESS_REASONING_EFFORT)
rounds = 1            # improvement rounds driven by surviving mutants
max_repairs = 2       # retries when a generated test file fails validation
max_mutants = 40      # undetected mutants sent to the model per round
runner = "auto"       # how to run Vitest and Stryker: "bun", "npx", or "auto"
"""


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = DEFAULT_MODEL
    reasoning_effort: str | None = None
    rounds: int = Field(default=1, ge=0, le=5)
    max_repairs: int = Field(default=2, ge=0, le=5)
    max_mutants: int = Field(default=40, ge=1, le=200)
    runner: Literal["auto", "bun", "npx"] = "auto"
    vitest_timeout: int = Field(default=300, ge=10)
    stryker_timeout: int = Field(default=1800, ge=30)


def load_settings(root: Path, **overrides: object) -> Settings:
    """Defaults < regress.toml < environment < command-line overrides (None values are ignored)."""
    data: dict[str, object] = {}
    path = root / CONFIG_FILE
    if path.is_file():
        try:
            data.update(tomllib.loads(path.read_text()))
        except tomllib.TOMLDecodeError as error:
            raise ProjectError(f"Invalid {CONFIG_FILE}: {error}") from error
    for key, variable in (("model", "REGRESS_MODEL"), ("reasoning_effort", "REGRESS_REASONING_EFFORT")):
        if os.environ.get(variable):
            data[key] = os.environ[variable]
    data.update({key: value for key, value in overrides.items() if value is not None})
    try:
        return Settings.model_validate(data)
    except ValidationError as error:
        details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors())
        raise ProjectError(f"Invalid configuration ({CONFIG_FILE} or flags): {details}") from error
