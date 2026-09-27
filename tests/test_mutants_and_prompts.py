import pytest
from conftest import load_fixture

from regress.models import Mutant, MutantStatus, MutationRun
from regress.mutants import describe_mutant, mutated_line, select_mutants, short_description
from regress.prompts import PromptContext, generate_prompt, improve_prompt, repair_prompt
from regress.stryker import parse_stryker_report

SOURCE = load_fixture("stryker-report.json")["files"]["src/cart.ts"]["source"]
LINES = SOURCE.splitlines(keepends=True)


def mutant(id: str, line: int, mutator: str = "EqualityOperator", status: str = "Survived") -> Mutant:
    return Mutant(
        id=id,
        mutator=mutator,
        status=MutantStatus(status),
        replacement="x",
        original="y",
        start_line=line,
        start_column=1,
        end_line=line,
        end_column=2,
    )


def test_select_mutants_prefers_distinct_lines_and_mutators():
    run = MutationRun(
        index=1, mutants=[mutant("1", 1), mutant("2", 1), mutant("3", 2), mutant("4", 3, status="Killed")]
    )
    assert [m.id for m in select_mutants(run, limit=2)] == ["1", "3"]
    assert [m.id for m in select_mutants(run, limit=5)] == ["1", "2", "3"]
    assert [m.id for m in select_mutants(run, limit=5, exclude={"1"})] == ["2", "3"]


def test_describes_single_line_mutant_as_before_and_after():
    run = parse_stryker_report(load_fixture("stryker-report.json"), "src/cart.ts", 1)
    m = next(m for m in run.mutants if m.id == "3")
    text = describe_mutant(m, LINES, "src/cart.ts")
    assert "Mutant 3 · EqualityOperator · src/cart.ts:2" in text
    assert "original: if (qty >= max) {" in text
    assert "mutated:  if (qty > max) {" in text
    assert short_description(m, LINES) == "if (qty >= max) {  →  if (qty > max) {"


def test_describes_multiline_block_removal():
    run = parse_stryker_report(load_fixture("stryker-report.json"), "src/cart.ts", 1)
    m = next(m for m in run.mutants if m.id == "5")
    text = describe_mutant(m, LINES, "src/cart.ts")
    assert "original (lines 2-4)" in text
    assert 'throw new Error("too many");' in text
    assert "replaced with:\n    {}" in text


def test_short_description_ignores_a_source_edited_after_the_run():
    # `regress report` reads the source from disk, which may have changed since the run.
    run = parse_stryker_report(load_fixture("stryker-report.json"), "src/cart.ts", 1)
    m = next(m for m in run.mutants if m.id == "3")
    fallback = "qty >= max  →  qty > max"
    assert short_description(m, ["export const x = 1;\n"]) == fallback  # file got shorter
    assert short_description(m, ["// new header\n", *LINES]) == fallback  # lines shifted


@pytest.mark.parametrize(("value", "mutated"), [("1", "0"), ("11", "01")])
def test_mutant_descriptions_use_javascript_utf16_columns(value, mutated):
    source = f'const emoji = "😀"; return {value};\n'
    report = {
        "files": {
            "src/example.ts": {
                "source": source,
                "mutants": [
                    {
                        "id": "1",
                        "mutatorName": "NumberLiteral",
                        "status": "Survived",
                        "replacement": "0",
                        "location": {"start": {"line": 1, "column": 28}, "end": {"line": 1, "column": 29}},
                    }
                ],
            }
        }
    }
    [m] = parse_stryker_report(report, "src/example.ts", 1).mutants
    original_line = source.rstrip("\n")
    mutated_text = f'const emoji = "😀"; return {mutated};'

    assert m.original == "1"
    assert mutated_line(m, [source]) == (original_line, mutated_text)
    assert f"mutated:  {mutated_text}" in describe_mutant(m, [source], "src/example.ts")
    assert short_description(m, [source]) == f"{original_line}  →  {mutated_text}"


@pytest.mark.parametrize(
    ("start_line", "start_column", "end_column"),
    [(0, 1, 2), (1, 0, 1), (1, 1, 100), (1, 3, 2), (1, 16, 17)],
)
def test_mutant_description_ignores_invalid_or_stale_coordinates(start_line, start_column, end_column):
    source = 'const emoji = "😀"; return 1;'
    m = mutant("1", start_line).model_copy(
        update={
            "start_column": start_column,
            "end_column": end_column,
            "original": source[start_column - 1 : end_column - 1],
        }
    )
    # Columns 16..17 split the emoji's surrogate pair, so cannot identify a valid source span.
    assert mutated_line(m, [source]) is None


CTX = PromptContext(
    source_rel="src/cart.ts", source=SOURCE, test_rel="test/cart.test.ts", import_path="../src/cart", language="ts"
)


def test_generate_prompt_numbers_source_and_includes_tests():
    prompt = generate_prompt(CTX, "it('x', () => {})")
    assert 'Import the module under test from "../src/cart"' in prompt
    assert " 2 |   if (qty >= max) {" in prompt
    assert "it('x', () => {})" in prompt


def test_generate_prompt_without_tests_asks_to_create_file():
    assert "does not exist yet" in generate_prompt(CTX, None)


def test_improve_prompt_lists_mutants():
    prompt = improve_prompt(CTX, "tests", 4, ["Mutant 3 · EqualityOperator"])
    assert "all 4 tests pass" in prompt
    assert "Mutant 3 · EqualityOperator" in prompt
    assert "equivalent_mutants" in prompt


def test_repair_prompt_quotes_candidate_and_problems():
    prompt = repair_prompt("TASK", "ts", "bad file", ["Test x fails"])
    assert prompt.startswith("TASK")
    assert "```ts\nbad file\n```" in prompt
    assert "- Test x fails" in prompt


def test_fences_grow_to_avoid_collisions():
    prompt = repair_prompt("TASK", "ts", "const s = `\n```\n`;", ["p"])
    assert "````ts" in prompt
