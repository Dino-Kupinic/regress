from __future__ import annotations

from regress.models import Mutant, MutantStatus, MutationRun

_STATUS_NOTES = {
    MutantStatus.SURVIVED: "survived: tests ran this code but no assertion failed",
    MutantStatus.NO_COVERAGE: "no coverage: no test executes this code",
}


def select_mutants(run: MutationRun, limit: int, exclude: set[str] | frozenset[str] = frozenset()) -> list[Mutant]:
    """Undetected mutants worth sending to the model, spread across the file.

    One mutant per (line, mutator) is chosen before any duplicates, so a cap still covers as
    many distinct weak spots as possible.
    """
    candidates = [m for m in run.undetected if m.id not in exclude]
    first, rest, seen = [], [], set()
    for mutant in candidates:
        key = (mutant.start_line, mutant.mutator)
        (rest if key in seen else first).append(mutant)
        seen.add(key)
    return sorted((first + rest)[:limit], key=lambda m: (m.start_line, m.start_column))


def mutated_line(mutant: Mutant, source_lines: list[str]) -> tuple[str, str] | None:
    """(original, mutated) text of the line a single-line mutant changes.

    None for multi-line mutants, and when the source no longer matches the mutant (it was edited
    after the run that found it).
    """
    if mutant.start_line != mutant.end_line or "\n" in mutant.replacement or mutant.start_line > len(source_lines):
        return None
    line = source_lines[mutant.start_line - 1].rstrip("\n")
    if line[mutant.start_column - 1 : mutant.end_column - 1] != mutant.original:
        return None
    mutated = line[: mutant.start_column - 1] + mutant.replacement + line[mutant.end_column - 1 :]
    return line.strip(), mutated.strip()


def describe_mutant(mutant: Mutant, source_lines: list[str], source_rel: str) -> str:
    note = _STATUS_NOTES.get(mutant.status, mutant.status.value)
    header = f"Mutant {mutant.id} · {mutant.mutator} · {source_rel}:{mutant.start_line} ({note})"
    pair = mutated_line(mutant, source_lines)
    if pair:
        original, mutated = pair
        return f"{header}\n  original: {original}\n  mutated:  {mutated}"
    snippet = _clip(mutant.original, 8)
    replacement = _clip(mutant.replacement, 4) or "(removed)"
    return (
        f"{header}\n  original (lines {mutant.start_line}-{mutant.end_line}):\n{_indent(snippet)}\n"
        f"  replaced with:\n{_indent(replacement)}"
    )


def short_description(mutant: Mutant, source_lines: list[str]) -> str:
    """One-line `original → mutated` summary for terminal reports."""
    pair = mutated_line(mutant, source_lines)
    if pair:
        return f"{_one_line(pair[0])}  →  {_one_line(pair[1])}"
    return f"{_one_line(mutant.original)}  →  {_one_line(mutant.replacement) or '(removed)'}"


def _clip(text: str, max_lines: int) -> str:
    lines = text.strip("\n").splitlines()
    if len(lines) > max_lines:
        lines = [*lines[:max_lines], f"... ({len(lines) - max_lines} more lines)"]
    return "\n".join(lines)


def _indent(text: str) -> str:
    return "\n".join(f"    {line}" for line in text.splitlines())


def _one_line(text: str, width: int = 70) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= width else collapsed[: width - 1] + "…"
