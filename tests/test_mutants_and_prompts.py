from conftest import load_fixture

from regress.models import Mutant, MutantStatus, MutationRun
from regress.mutants import describe_mutant, select_mutants, short_description
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
