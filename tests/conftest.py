from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from regress.errors import LLMError
from regress.llm import Completion, TestFileProposal

FIXTURES = Path(__file__).parent / "fixtures"
EXAMPLES = Path(__file__).parent.parent / "examples"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class ScriptedLLM:
    """Stands in for the model: returns prepared test files in order and records the prompts."""

    model = "scripted"

    def __init__(self, *responses: str | TestFileProposal) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def propose(self, instructions: str, prompt: str, on_status=None, on_warning=None) -> Completion:
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("ScriptedLLM ran out of responses")
        item = self.responses.pop(0)
        if isinstance(item, str):
            item = TestFileProposal(summary="scripted", new_tests=[], equivalent_mutants=[], test_file=item)
        return Completion(item, input_tokens=100, output_tokens=50)


class ScriptedFactory:
    """Builds a ScriptedLLM in the worker process of a run started over HTTP (it must pickle)."""

    def __init__(self, *responses: str) -> None:
        self.responses = responses

    def __call__(self, model: str, settings: object) -> ScriptedLLM:
        return ScriptedLLM(*self.responses)


class SlowLLM:
    """A model that takes far too long, for cancelling runs mid-call."""

    model = "slow"

    def propose(self, instructions: str, prompt: str, on_status=None, on_warning=None) -> Completion:
        time.sleep(60)
        raise AssertionError("SlowLLM should have been cancelled")


class FailingLLM:
    model = "failing"

    def propose(self, instructions: str, prompt: str, on_status=None, on_warning=None) -> Completion:
        raise LLMError("The model is down.")


def slow_llm(model: str, settings: object) -> SlowLLM:
    return SlowLLM()


def failing_llm(model: str, settings: object) -> FailingLLM:
    return FailingLLM()


def make_node_package(root: Path, name: str, version: str) -> None:
    package = root / "node_modules" / name
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"name": name, "version": version}))


@pytest.fixture(autouse=True)
def isolated_user_env(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never read or write the real user config/cache, and never reach the OpenAI API by accident."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / "cache"))
    for variable in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "REGRESS_PROVIDER",
        "REGRESS_MODEL",
        "REGRESS_REASONING_EFFORT",
    ):
        monkeypatch.delenv(variable, raising=False)
    return home


@pytest.fixture
def js_project(tmp_path: Path) -> Path:
    """A minimal fake JS project with the required packages 'installed'."""
    (tmp_path / "package.json").write_text("{}")
    make_node_package(tmp_path, "vitest", "4.1.0")
    make_node_package(tmp_path, "@stryker-mutator/core", "10.0.0")
    make_node_package(tmp_path, "@stryker-mutator/vitest-runner", "10.0.0")
    (tmp_path / "src").mkdir()
    return tmp_path


def examples_ready() -> bool:
    return (EXAMPLES / "node_modules" / "vitest").is_dir() and shutil.which("bun") is not None


requires_examples = pytest.mark.skipif(
    not examples_ready(), reason="run `bun install` in examples/ (and install Bun) for integration tests"
)
