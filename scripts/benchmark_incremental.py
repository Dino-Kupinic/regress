"""Compare incremental improvement rounds with full runs, without model calls.

Run from a checkout after installing examples/: uv run python scripts/benchmark_incremental.py
Add --larger examples/defu for the upstream defu test suite (checkout required).
All source/test edits and tool artifacts live in temporary copies.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from statistics import median
from time import perf_counter

from regress.errors import ToolError
from regress.evaluation import load_suites
from regress.project import load_project
from regress.stryker import run_stryker


def measure(root: Path, source: str, test: str, before: str, after: str, trials: int) -> dict:
    project = load_project(root / source, root / test)
    samples = []
    for trial in range(trials):
        project.test_file.parent.mkdir(parents=True, exist_ok=True)
        project.test_file.write_text(before)
        directory = root / ".regress" / f"benchmark-{Path(source).stem}-{trial}"
        run_stryker(project, directory / "incremental", 1)
        project.test_file.write_text(after)
        # Alternate order to reduce filesystem-cache/order bias.
        results = {}
        for incremental in [True, False] if trial % 2 == 0 else [False, True]:
            started = perf_counter()
            result = run_stryker(
                project,
                directory / ("incremental" if incremental else "full"),
                2 if incremental else 1,
                incremental=incremental,
            )
            results[incremental] = (result, perf_counter() - started)
        warm, warm_time = results[True]
        full, full_time = results[False]
        assert {m.id: m.status for m in warm.mutants} == {m.id: m.status for m in full.mutants}, source
        assert warm.score == full.score, source
        samples.append(
            {
                "incremental_seconds": warm_time,
                "full_seconds": full_time,
                "reused": warm.reused_mutants,
                "total": len(warm.mutants),
                "score": warm.score,
            }
        )
    warm_time = median(s["incremental_seconds"] for s in samples)
    full_time = median(s["full_seconds"] for s in samples)
    return {
        "source": source,
        "incremental_seconds": round(warm_time, 3),
        "full_seconds": round(full_time, 3),
        "speedup": round(full_time / warm_time, 3),
        "samples": samples,
    }


def copy_project(original: Path, root: Path, dependencies: Path) -> None:
    root.mkdir()
    shutil.copy(original / "package.json", root / "package.json")
    for name in ("src", "test", "oracle"):
        if (original / name).is_dir():
            shutil.copytree(original / name, root / name)
    for pattern in ("vitest.config.*", "vite.config.*"):
        for config in original.glob(pattern):
            shutil.copy(config, root / config.name)
    (root / "node_modules").symlink_to(dependencies, target_is_directory=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", type=Path, default=Path(__file__).resolve().parents[1] / "examples")
    parser.add_argument(
        "--larger", type=Path, help="Path to an upstream defu checkout for the larger-example comparison."
    )
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("incremental-benchmark.json"))
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials must be positive")
    examples = args.examples.resolve()
    dependencies = examples / "node_modules"
    results = []
    with tempfile.TemporaryDirectory(prefix="regress-incremental-benchmark-") as temporary:
        root = (Path(temporary) / "examples").resolve()
        copy_project(examples, root, dependencies)
        for suite in load_suites(examples):
            oracle = (examples / suite.oracle).read_text()
            test_file = examples / suite.test
            # Pagination has no initial test file. Seed it with the oracle, then add
            # a smoke case so the measured round still edits the test file.
            before = test_file.read_text() if test_file.is_file() else oracle
            after = (
                oracle
                if test_file.is_file()
                else oracle
                + '\ndescribe("additional pagination cases", () => { it("smoke", () => { expect(true).toBe(true); }); });\n'
            )
            result = measure(root, suite.source, suite.test, before, after, args.trials)
            result["project"] = "examples"
            results.append(result)
            print(json.dumps(result), flush=True)
        if args.larger:
            original = args.larger.resolve()
            root = (Path(temporary) / "defu").resolve()
            copy_project(original, root, dependencies)
            before = (root / "test/defu.test.ts").read_text()
            after = (
                before
                + '\nit("merges another pair of objects", () => { expect(defu({ a: 1 }, { b: 2 })).toEqual({ a: 1, b: 2 }); });\n'
            )
            result = measure(root, "src/defu.ts", "test/defu.test.ts", before, after, args.trials)
            result["project"] = "defu"
            results.append(result)
            print(json.dumps(result), flush=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    try:
        main()
    except ToolError as error:
        print(error.output)
        raise
