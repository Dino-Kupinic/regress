from __future__ import annotations

from dataclasses import dataclass

from regress.project import Project, related_files

MAX_SOURCE_LINES = 1500
MAX_RELATED_LINES = 300

INSTRUCTIONS = """\
You are Regress, an expert test engineer who writes Vitest unit tests for TypeScript and JavaScript.

Hard rules:
- You may only write the test file. Never modify, re-implement, copy, or mock the module under test.
- The implementation is the source of truth. Tests pin down its CURRENT behavior, so every test must pass \
against the code exactly as shown. Work out expected values by tracing the code carefully.
- Keep every existing test with its exact name and behavior. You may add imports, tests, and describe blocks.
- Import test APIs from "vitest" and the module under test with the import path you are given.
- Write behavioral tests through the public API: concrete inputs, exact expected outputs, thrown errors, \
state changes, and boundary values. Prefer toBe/toEqual/toStrictEqual/toThrow with specific values over \
weak assertions such as toBeDefined or toBeTruthy.
- Keep tests deterministic: no snapshots, .only, .skip, .todo, network, filesystem, real timers or real \
randomness. Inject fakes where the API allows it, or use vi.useFakeTimers / vi.spyOn.
- Return the complete test file in `test_file`, not a diff.
"""


@dataclass(frozen=True)
class PromptContext:
    source_rel: str
    source: str
    test_rel: str
    import_path: str
    language: str
    related: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_project(cls, project: Project) -> PromptContext:
        related = tuple(
            (path.relative_to(project.root).as_posix(), _clip_lines(path.read_text(), MAX_RELATED_LINES))
            for path in related_files(project.source, project.root)
        )
        return cls(
            source_rel=project.source_rel,
            source=project.source.read_text(),
            test_rel=project.test_rel,
            import_path=project.import_path,
            language="ts" if project.source.suffix in (".ts", ".tsx", ".mts", ".cts") else "js",
            related=related,
        )


def generate_prompt(ctx: PromptContext, current_tests: str | None) -> str:
    return "\n\n".join(
        [
            "# Task: write unit tests",
            _header(ctx),
            _current_tests(ctx, current_tests),
            "## Instructions\n"
            f"Extend the test file with focused tests that pin down the behavior of `{ctx.source_rel}`:\n"
            "- every exported function, class and method, including error paths and error messages,\n"
            "- boundary values of every comparison: just below, exactly at, and just above each limit,\n"
            "- every branch and every distinct return value,\n"
            "- exact results of arithmetic and rounding rather than approximate checks.\n"
            "Keep all existing tests exactly as they are. Return the complete test file.",
        ]
    )


def improve_prompt(ctx: PromptContext, current_tests: str, test_count: int, mutants: list[str]) -> str:
    return "\n\n".join(
        [
            "# Task: strengthen the tests using mutation testing results",
            _header(ctx),
            _current_tests(ctx, current_tests, note=f"all {test_count} tests pass"),
            "## Mutants the current tests fail to detect\n"
            f"Mutation testing made small changes (mutants) to `{ctx.source_rel}` and re-ran the tests. "
            "None of the mutants below made any test fail, so each one is a regression the suite would miss.\n\n"
            + "\n\n".join(mutants),
            "## Instructions\n"
            "Add tests so that each mutant above would make at least one test fail, while every test still "
            "passes against the original code.\n"
            "- For each mutant, find a concrete input where the original and the mutated code behave "
            "differently (return value, thrown error, state change) and assert on that behavior exactly.\n"
            '- Name tests after the behavior they check (e.g. "rejects quantities above the maximum"), '
            "not after mutant IDs.\n"
            "- Some mutants may be equivalent: no input can tell them apart from the original. List their "
            "IDs in `equivalent_mutants` instead of writing contorted tests.\n"
            "Keep all existing tests exactly as they are. Return the complete test file.",
        ]
    )


def repair_prompt(task_prompt: str, language: str, candidate: str, problems: list[str]) -> str:
    listing = "\n".join(f"- {problem}" for problem in problems)
    return "\n\n".join(
        [
            task_prompt,
            "## Your previous attempt was rejected\n" + _fence(candidate, language),
            "Problems:\n" + listing,
            "Fix these problems and return the complete corrected test file. The implementation is the source "
            "of truth: when a test's expectation disagrees with what the code actually does, fix the "
            "expectation or drop that test. Never assume the code is wrong.",
        ]
    )


def _header(ctx: PromptContext) -> str:
    parts = [
        f"Module under test: `{ctx.source_rel}`\n"
        f'Test file: `{ctx.test_rel}`. Import the module under test from "{ctx.import_path}".\n'
        'Test framework: Vitest (import describe, it, expect, vi from "vitest").',
        f"## Source of {ctx.source_rel} (line numbers are for reference only)\n"
        + _fence(_numbered(_clip_lines(ctx.source, MAX_SOURCE_LINES)), ctx.language),
    ]
    if ctx.related:
        related = "\n\n".join(f"### {path}\n{_fence(code, ctx.language)}" for path, code in ctx.related)
        parts.append("## Related code (read-only context)\n" + related)
    return "\n\n".join(parts)


def _current_tests(ctx: PromptContext, current_tests: str | None, note: str | None = None) -> str:
    if not current_tests or not current_tests.strip():
        return f"## Current test file ({ctx.test_rel})\nThe test file does not exist yet. Create it from scratch."
    suffix = f", {note}" if note else ""
    return f"## Current test file ({ctx.test_rel}{suffix})\n{_fence(current_tests, ctx.language)}"


def _numbered(code: str) -> str:
    lines = code.splitlines()
    width = len(str(len(lines)))
    return "\n".join(f"{i:>{width}} | {line}" for i, line in enumerate(lines, start=1))


def _clip_lines(text: str, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join([*lines[:max_lines], f"// ... truncated {len(lines) - max_lines} more lines"])


def _fence(code: str, language: str) -> str:
    fence = "```"
    while fence in code:
        fence += "`"
    return f"{fence}{language}\n{code.rstrip()}\n{fence}"
