from pathlib import Path
from types import SimpleNamespace

import pytest

from regress.config import load_settings
from regress.errors import LLMError, ProjectError
from regress.llm import OpenAILLM, TestFileProposal
from regress.project import Project, Toolchain
from regress.validate import clean_test_file, static_problems


@pytest.fixture
def project(tmp_path: Path) -> Project:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/cart.ts").write_text("export const x = 1;\n")
    return Project(
        root=tmp_path,
        source=tmp_path / "src/cart.ts",
        test_file=tmp_path / "test/cart.test.ts",
        toolchain=Toolchain("bun", "4.1.0", "10.0.0", "10.0.0"),
    )


GOOD = 'import { it, expect } from "vitest";\nimport { x } from "../src/cart";\nit("x", () => expect(x).toBe(1));\n'


def test_accepts_a_plain_test_file(project):
    assert static_problems(project, GOOD, None) == []


def test_requires_importing_the_module_under_test(project):
    problems = static_problems(project, 'import { it } from "vitest";\nit("x", () => {});', None)
    assert any('import the module under test from "../src/cart"' in p for p in problems)


def test_rejects_mocking_the_module_under_test(project):
    problems = static_problems(project, GOOD + 'vi.mock("../src/cart");\n', None)
    assert any("Do not mock" in p for p in problems)


def test_rejects_only_and_new_skips(project):
    assert any(".only" in p for p in static_problems(project, GOOD + 'it.only("y", () => {});', None))
    skipped = GOOD + 'it.skip("y", () => {});'
    assert any(".skip" in p for p in static_problems(project, skipped, GOOD))
    # A skip that was already there is not the model's doing.
    assert static_problems(project, skipped, skipped) == []


def test_clean_test_file_strips_markdown_fences():
    assert clean_test_file("```ts\nconst a = 1;\n```") == "const a = 1;\n"
    assert clean_test_file("const a = 1;") == "const a = 1;\n"


def test_settings_precedence(tmp_path, monkeypatch):
    (tmp_path / "regress.toml").write_text('model = "from-file"\nrounds = 2\n')
    assert load_settings(tmp_path).model == "from-file"
    monkeypatch.setenv("REGRESS_MODEL", "from-env")
    settings = load_settings(tmp_path, rounds=None)
    assert (settings.model, settings.rounds) == ("from-env", 2)
    assert load_settings(tmp_path, model="from-flag", rounds=0).model == "from-flag"


def test_settings_reject_unknown_keys_and_bad_values(tmp_path):
    (tmp_path / "regress.toml").write_text("modle = 'typo'\n")
    with pytest.raises(ProjectError, match="modle"):
        load_settings(tmp_path)
    (tmp_path / "regress.toml").write_text("rounds = 99\n")
    with pytest.raises(ProjectError, match="rounds"):
        load_settings(tmp_path)


class FakeResponses:
    def __init__(self, parsed):
        self.parsed = parsed
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=12, output_tokens=34)
        return SimpleNamespace(output_parsed=self.parsed, usage=usage, status="completed", incomplete_details=None)


def test_openai_call_uses_structured_output_without_storage():
    proposal = TestFileProposal(summary="s", new_tests=["t"], equivalent_mutants=[], test_file="code")
    responses = FakeResponses(proposal)
    llm = OpenAILLM("gpt-test", reasoning_effort="high", client=SimpleNamespace(responses=responses))
    completion = llm.propose("INSTRUCTIONS", "PROMPT")
    call = responses.calls[0]
    assert call["model"] == "gpt-test"
    assert call["text_format"] is TestFileProposal
    assert call["store"] is False
    assert call["reasoning"] == {"effort": "high"}
    assert (call["instructions"], call["input"]) == ("INSTRUCTIONS", "PROMPT")
    assert (completion.proposal, completion.input_tokens, completion.output_tokens) == (proposal, 12, 34)


def test_openai_empty_output_is_an_llm_error():
    llm = OpenAILLM("gpt-test", client=SimpleNamespace(responses=FakeResponses(None)))
    with pytest.raises(LLMError, match="no usable test file"):
        llm.propose("i", "p")


def test_missing_api_key_is_reported(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        OpenAILLM("gpt-test")


def test_proposal_schema_is_valid_for_strict_structured_outputs():
    from openai.lib._pydantic import to_strict_json_schema

    schema = to_strict_json_schema(TestFileProposal)
    assert set(schema["required"]) == {"summary", "new_tests", "equivalent_mutants", "test_file"}
    assert schema["additionalProperties"] is False
