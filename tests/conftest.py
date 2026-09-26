from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

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

    def propose(self, instructions: str, prompt: str) -> Completion:
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("ScriptedLLM ran out of responses")
        item = self.responses.pop(0)
        if isinstance(item, str):
            item = TestFileProposal(summary="scripted", new_tests=[], equivalent_mutants=[], test_file=item)
        return Completion(item, input_tokens=100, output_tokens=50)


def make_node_package(root: Path, name: str, version: str) -> None:
    package = root / "node_modules" / name
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"name": name, "version": version}))


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
