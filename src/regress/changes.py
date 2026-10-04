"""Which source lines changed since a git ref, so mutation testing can stay on them."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from regress.errors import ProjectError
from regress.project import SOURCE_EXTENSIONS

LineRange = tuple[int, int]  # 1-based, both ends included

GIT_TIMEOUT = 60
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
# Lines Stryker has nothing to mutate on: blank lines, comments and imports.
_TRIVIAL = re.compile(r"^\s*(?:$|//|/\*|\*(?:\s|/|$)|import\s|}\s*from\s)")


def changed_lines(root: Path, ref: str) -> dict[Path, list[LineRange]]:
    """Lines added or changed in each file under `root` since `ref`, including uncommitted and untracked files.

    The diff is taken against the merge base of `ref` and HEAD, so `main` means "what this branch changed",
    not what happened on main since. An untracked file counts as changed throughout.
    """
    if ref.startswith("-"):
        raise ProjectError(f"Not a git ref: {ref}")
    diff = _git(root, "diff", "--merge-base", ref, "--unified=0", "--no-color", "--no-ext-diff", "--relative")
    changes = parse_diff(diff, root)
    for name in _git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        path = root / name
        if name and path.suffix in SOURCE_EXTENSIONS:
            try:
                count = len(path.read_text(encoding="utf-8").splitlines())
            except (OSError, UnicodeDecodeError):
                continue
            if count:
                changes[path] = [(1, count)]
    return changes


def parse_diff(diff: str, root: Path) -> dict[Path, list[LineRange]]:
    """Added line ranges per file in a `git diff --unified=0` of the working tree."""
    changes: dict[Path, list[LineRange]] = {}
    current: list[LineRange] | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            name = line[4:]
            if name == "/dev/null":  # a deleted file
                current = None
                continue
            name = name.removeprefix("b/")
            current = changes.setdefault(root / name, [])
            continue
        match = _HUNK.match(line)
        if match and current is not None:
            start, count = int(match.group(1)), int(match.group(2) or "1")
            if count:  # count 0: lines were only removed here
                current.append((start, start + count - 1))
    return {path: merge_ranges(ranges) for path, ranges in changes.items() if ranges}


def merge_ranges(ranges: list[LineRange]) -> list[LineRange]:
    """Sorted ranges with overlapping and adjacent ones joined."""
    merged: list[LineRange] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def code_ranges(source: str, ranges: list[LineRange]) -> list[LineRange]:
    """The ranges trimmed to lines with code on them; ranges of only comments, imports or blanks are dropped."""
    lines = source.splitlines()
    trimmed: list[LineRange] = []
    for start, end in ranges:
        end = min(end, len(lines))
        while start <= end and _TRIVIAL.match(lines[start - 1]):
            start += 1
        while end >= start and _TRIVIAL.match(lines[end - 1]):
            end -= 1
        if start <= end:
            trimmed.append((start, end))
    return trimmed


def format_ranges(ranges: list[LineRange] | tuple[LineRange, ...]) -> str:
    """`12-20, 31` for a short label."""
    return ", ".join(str(start) if start == end else f"{start}-{end}" for start, end in ranges)


def _git(root: Path, *args: str) -> str:
    if not shutil.which("git"):
        raise ProjectError("--changed needs git on PATH.")
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ProjectError(f"`git {args[0]}` timed out after {GIT_TIMEOUT}s.") from error
    if result.returncode != 0:
        message = result.stderr.strip().splitlines()[-1:] or [f"exit code {result.returncode}"]
        raise ProjectError(f"`git {args[0]}` failed: {message[0]}")
    return result.stdout
