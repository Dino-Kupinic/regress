"""Changed-line detection, multi-file target resolution, and the multi-file run loop."""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import ScriptedLLM
from typer.testing import CliRunner

import regress.batch as batch
from regress.batch import BatchReport, FileResult, Target, resolve_targets, run_batch
from regress.changes import changed_lines, code_ranges, format_ranges, merge_ranges, parse_diff
from regress.cli import app
from regress.errors import ProjectError, RegressError
from regress.models import Mutant, MutantStatus, MutationRun, RunReport, Stage
from regress.pipeline import RunOptions
from regress.project import Project, Toolchain
from regress.store import RunStore
from regress.stryker import stryker_config

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

DIFF = """\
diff --git a/src/cart.ts b/src/cart.ts
index 1111111..2222222 100644
--- a/src/cart.ts
+++ b/src/cart.ts
@@ -3,0 +4,2 @@ export class Cart {
+  a
+  b
@@ -10 +12 @@ export class Cart {
-  old
+  new
@@ -20,3 +22,0 @@ export class Cart {
-  removed
-  removed
-  removed
@@ -30 +31,3 @@
+  x
diff --git a/src/gone.ts b/src/gone.ts
deleted file mode 100644
--- a/src/gone.ts
+++ /dev/null
@@ -1,2 +0,0 @@
-a
-b
diff --git a/src/only-removed.ts b/src/only-removed.ts
--- a/src/only-removed.ts
+++ b/src/only-removed.ts
@@ -4 +3,0 @@
-x
"""


def test_parse_diff_keeps_added_line_ranges(tmp_path):
    changes = parse_diff(DIFF, tmp_path)

    # Pure removals (count 0) and deleted files add nothing to mutate.
    assert changes == {tmp_path / "src/cart.ts": [(4, 5), (12, 12), (31, 33)]}


def test_merge_ranges_joins_overlapping_and_adjacent_ranges():
    assert merge_ranges([(10, 12), (1, 2), (3, 4), (11, 20), (30, 30)]) == [(1, 4), (10, 20), (30, 30)]


def test_code_ranges_trims_comments_imports_and_blank_lines():
    source = "\n".join(
        [
            'import { x } from "./x";',  # 1
            "",  # 2
            "// a comment",  # 3
            "export function f(a: number) {",  # 4
            "  return a + 1;",  # 5
            "}",  # 6
            "/**",  # 7
            " * docs",  # 8
            " */",  # 9
            "class A { *items() { yield 1; } }",  # 10: a generator method is code, not a comment
        ]
    )

    assert code_ranges(source, [(1, 5)]) == [(4, 5)]
    assert code_ranges(source, [(1, 3), (7, 9)]) == []
    assert code_ranges(source, [(9, 10)]) == [(10, 10)]
    assert code_ranges(source, [(6, 99)]) == [(6, 10)]  # clamped to the file; inner comments stay


def test_format_ranges():
    assert format_ranges([(3, 3), (10, 14)]) == "3, 10-14"


def _project(root: Path, lines: tuple[tuple[int, int], ...] = ()) -> Project:
    toolchain = Toolchain(runner="npx", vitest="4.1.0", stryker="10.0.0", vitest_runner="10.0.0")
    return Project(root, root / "src/cart.ts", root / "test/cart.test.ts", toolchain, mutate_lines=lines)


def test_stryker_mutates_only_the_changed_lines(tmp_path):
    whole = stryker_config(_project(tmp_path), tmp_path / "r.json", tmp_path / "tmp")
    ranged = stryker_config(_project(tmp_path, ((4, 5), (12, 12))), tmp_path / "r.json", tmp_path / "tmp")

    assert whole["mutate"] == ["src/cart.ts"]
    assert ranged["mutate"] == ["src/cart.ts:4-5", "src/cart.ts:12-12"]
    assert ranged["testFiles"] == ["test/cart.test.ts"]


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(js_project: Path) -> Path:
    """A git repository whose project has two committed sources, a test, and a nested project."""
    root = js_project
    (root / "src/a.ts").write_text("export const a = 1;\nexport const b = 2;\nexport const c = 3;\n")
    (root / "src/b.ts").write_text("export const d = 4;\n")
    (root / "test").mkdir()
    (root / "test/a.test.ts").write_text("// test\n")
    (root / "vendor").mkdir()
    (root / "vendor/package.json").write_text("{}")
    (root / "vendor/lib.ts").write_text("export const v = 1;\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    _git(root, "checkout", "-qb", "feature")
    return root


@requires_git
def test_changed_lines_covers_committed_uncommitted_and_untracked_changes(repo):
    (repo / "src/a.ts").write_text("export const a = 1;\nexport const b = 20;\nexport const c = 3;\n")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "change b")
    (repo / "src/b.ts").write_text("export const d = 4;\nexport const e = 5;\n")  # uncommitted
    (repo / "src/new.ts").write_text("export const n = 1;\nexport const m = 2;\n")  # untracked

    changes = changed_lines(repo, "main")

    assert changes[repo / "src/a.ts"] == [(2, 2)]
    assert changes[repo / "src/b.ts"] == [(2, 2)]
    assert changes[repo / "src/new.ts"] == [(1, 2)]


@requires_git
def test_changed_lines_ignores_what_changed_on_the_base_branch_since(repo):
    _git(repo, "checkout", "-q", "main")
    (repo / "src/b.ts").write_text("export const d = 40;\n")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "main moves on")
    _git(repo, "checkout", "-q", "feature")

    assert changed_lines(repo, "main") == {}


@requires_git
def test_changed_lines_reports_an_unknown_ref(repo):
    with pytest.raises(ProjectError, match="git diff"):
        changed_lines(repo, "no-such-branch")
    with pytest.raises(ProjectError, match="Not a git ref"):
        changed_lines(repo, "--output=/tmp/x")


def test_resolve_targets_expands_directories_to_source_files(repo):
    root, targets = resolve_targets([repo / "src", repo / "src/a.ts"])

    assert root == repo
    # Each file once; tests and the nested project's files never count.
    assert targets == [Target(repo / "src/a.ts"), Target(repo / "src/b.ts")]


def test_resolve_targets_rejects_paths_from_different_projects(repo):
    with pytest.raises(ProjectError, match="different projects"):
        resolve_targets([repo / "src", repo / "vendor/lib.ts"])
    with pytest.raises(ProjectError, match="No such file"):
        resolve_targets([repo / "src/missing.ts"])


@requires_git
def test_resolve_targets_with_changed_keeps_changed_sources_under_the_paths(repo):
    (repo / "src/a.ts").write_text("export const a = 1;\nexport const b = 20;\nexport const c = 3;\n")
    (repo / "test/a.test.ts").write_text("// changed test\n")
    (repo / "vendor/lib.ts").write_text("export const v = 2;\n")

    root, targets = resolve_targets([repo], "main")
    assert targets == [Target(repo / "src/a.ts", [(2, 2)])]

    _, targets = resolve_targets([repo / "src/b.ts"], "main")
    assert targets == []


def _stage(kind, score_counts: tuple[int, int], tests: int) -> Stage:
    killed, survived = score_counts
    mutants = [
        Mutant(
            id=str(i),
            mutator="M",
            status=MutantStatus.KILLED if i < killed else MutantStatus.SURVIVED,
            replacement="x",
            original="y",
            start_line=1,
            start_column=1,
            end_line=1,
            end_column=2,
        )
        for i in range(killed + survived)
    ]
    label = {"generated": "Generated tests", "improved": "Improved tests"}[kind]
    return Stage(kind=kind, label=label, test_count=tests, mutation=MutationRun(index=1, mutants=mutants))


class FakePipeline:
    """Stands in for Pipeline: scores or fails per file, as scripted by source name."""

    outcomes: dict[str, object] = {}

    def __init__(self, project, llm, options, reporter, store, batch_id=None) -> None:
        self.project, self.store, self.batch_id = project, store, batch_id

    def run(self) -> RunReport:
        self.run_dir = self.store.create(self.project.source)
        outcome = self.outcomes[self.project.source.name]
        if isinstance(outcome, BaseException):
            raise outcome
        first, kept = outcome
        return RunReport(
            id=self.run_dir.name,
            created_at="2026-10-04T12:00:00+00:00",
            project_root=str(self.project.root),
            source_file=self.project.source_rel,
            test_file=self.project.test_rel,
            test_file_existed=True,
            model="scripted",
            status="completed",
            stages=[first, kept],
            kept_stage=kept.label,
            mutate_lines=list(self.project.mutate_lines),
            batch_id=self.batch_id,
        )


@pytest.fixture
def fake_pipeline(monkeypatch):
    monkeypatch.setattr(batch, "Pipeline", FakePipeline)

    def load(source, test_file, runner):
        root = source.parents[1]
        return dataclasses.replace(_project(root), source=source, test_file=root / "test" / f"{source.stem}.test.ts")

    monkeypatch.setattr(batch, "load_project", load)
    return FakePipeline


def test_run_batch_keeps_going_after_a_failed_file_and_combines_scores(repo, fake_pipeline):
    (repo / "src/c.ts").write_text("// only a comment\n")
    fake_pipeline.outcomes = {
        "a.ts": (_stage("generated", (2, 8), 3), _stage("improved", (9, 1), 6)),
        "b.ts": RegressError("The existing tests do not pass."),
        "d.ts": (_stage("generated", (5, 5), 2), _stage("improved", (10, 0), 4)),
    }
    (repo / "src/d.ts").write_text("export const z = 1;\n")
    targets = [
        Target(repo / "src/a.ts", [(1, 2)]),
        Target(repo / "src/b.ts"),
        Target(repo / "src/c.ts", [(1, 1)]),
        Target(repo / "src/d.ts"),
    ]

    report = run_batch(repo, targets, ScriptedLLM(), RunOptions(), changed_since="main")

    a, b, c, d = report.files
    assert report.status == "failed"
    assert (a.status, a.mutate_lines, a.tests_first, a.tests_kept) == ("completed", [(1, 2)], 3, 6)
    assert (a.score_first, a.score_kept) == (20.0, 90.0)
    assert (b.status, b.error) == ("failed", "The existing tests do not pass.")
    assert b.run_id is not None  # its run directory holds what happened
    assert c.status == "skipped" and c.run_id is None
    assert d.status == "completed" and d.mutate_lines == []
    # 7 of 20 mutants detected first, 19 of 20 kept, over the two measured files.
    totals = report.totals
    assert (totals.files, totals.score_first, totals.score_kept) == (2, 35.0, 95.0)

    saved = BatchReport.model_validate_json(RunStore(repo).batch_path(report.id).read_text())
    assert saved == report


def test_run_batch_records_an_interrupted_file_and_stops(repo, fake_pipeline):
    fake_pipeline.outcomes = {"a.ts": KeyboardInterrupt(), "b.ts": RegressError("never reached")}

    with pytest.raises(KeyboardInterrupt):
        run_batch(repo, [Target(repo / "src/a.ts"), Target(repo / "src/b.ts")], ScriptedLLM(), RunOptions())

    [path] = (repo / ".regress/batches").iterdir()
    saved = BatchReport.model_validate_json(path.read_text())
    assert saved.status == "cancelled"
    assert [f.status for f in saved.files] == ["cancelled", "pending"]


def test_file_result_without_both_scores_is_left_out_of_the_totals():
    report = BatchReport(
        id="x",
        created_at="2026-10-04T12:00:00+00:00",
        project_root="/p",
        model="m",
        files=[
            FileResult(source_file="a.ts", status="completed", score_first=None, score_kept=50.0, valid_mutants=4),
            FileResult(source_file="b.ts", status="failed"),
        ],
    )
    assert report.totals.files == 0 and report.totals.score_kept is None


def test_cli_run_needs_a_source_or_changed():
    result = CliRunner().invoke(app, ["run"])
    assert result.exit_code == 1
    assert "--changed" in result.output


def test_cli_run_rejects_test_with_several_sources(repo):
    result = CliRunner().invoke(app, ["run", str(repo / "src"), "--test", str(repo / "test/a.test.ts")])
    assert result.exit_code == 1
    assert "--test works only with a single source file" in result.output


@requires_git
def test_cli_run_with_nothing_changed_says_so(repo, monkeypatch):
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(app, ["run", "--changed", "main"])
    assert result.exit_code == 0, result.output
    assert "No source files changed since main" in result.output


def test_project_mutate_lines_default_to_the_whole_file(tmp_path):
    project = _project(tmp_path)
    assert project.mutate_patterns == ["src/cart.ts"]
    assert dataclasses.replace(project, mutate_lines=((1, 3),)).mutate_patterns == ["src/cart.ts:1-3"]
