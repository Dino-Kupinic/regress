from pathlib import Path

import pytest

from regress.config import load_settings
from regress.errors import ProjectError
from regress.llm import TestFileProposal
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


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("model", "   "),
        ("model", "gpt-test\ninjected"),
        ("model", "x" * 257),
        ("reasoning_effort", "typo"),
        ("rounds", True),
        ("rounds", "2"),
        ("ask_model", "false"),
        ("llm_max_duration", 0),
        ("llm_max_output_tokens", 999999),
        ("vitest_timeout", 999999),
        ("stryker_timeout", 999999),
    ],
)
def test_settings_reject_ambiguous_and_unbounded_values(key, value):
    with pytest.raises(ProjectError, match=key):
        load_settings(None, **{key: value})


def test_unreadable_configuration_is_an_actionable_error(tmp_path):
    path = tmp_path / "regress.toml"
    path.write_bytes(b"\xff")
    with pytest.raises(ProjectError, match="Invalid .*regress.toml"):
        load_settings(tmp_path)
    path.unlink()
    path.mkdir()
    with pytest.raises(ProjectError, match="Invalid .*regress.toml"):
        load_settings(tmp_path)


def test_proposal_schema_is_valid_for_strict_structured_outputs():
    from openai.lib._pydantic import to_strict_json_schema

    schema = to_strict_json_schema(TestFileProposal)
    assert set(schema["required"]) == {"summary", "new_tests", "equivalent_mutants", "test_file"}
    assert schema["additionalProperties"] is False
