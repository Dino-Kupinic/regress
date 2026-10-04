from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from regress.errors import RegressError
from regress.files import atomic_write_text
from regress.models import RunReport

if TYPE_CHECKING:
    from regress.batch import BatchReport
    from regress.check import CheckReport

CHECK_FILE = "check.json"

REPORT_FILE = "report.json"


def regress_dir(root: Path) -> Path:
    """The project's .regress directory, created on demand and ignored by git."""
    directory = root / ".regress"
    _check_artifact_path(root, directory)
    directory.mkdir(exist_ok=True)
    gitignore = directory / ".gitignore"
    if not gitignore.exists():
        atomic_write_text(gitignore, "# Created by regress: run artifacts are local only.\n*\n")
    return directory


def _check_artifact_path(root: Path, path: Path) -> None:
    """Reject symlinked artifact directories before creating or reading files in them."""
    if not path.resolve().is_relative_to(root.resolve()):
        raise RegressError(f"Artifact directory {path} leads outside the project.")
    current = path
    while current != root:
        if current.is_symlink():
            raise RegressError(f"Artifact directory {current} must not be a symlink.")
        if current == current.parent:
            raise RegressError(f"Artifact directory {path} is outside the project.")
        current = current.parent


class RunStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.base = root / ".regress" / "runs"

    def create(self, source: Path) -> Path:
        regress_dir(self.root)
        _check_artifact_path(self.root, self.base)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", source.stem).strip("-").lower() or "run"
        run_dir = self.base / f"{stamp}-{slug}"
        suffix = 1
        self.base.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                run_dir.mkdir()
                return run_dir
            except FileExistsError:
                suffix += 1
                run_dir = self.base / f"{stamp}-{slug}-{suffix}"

    def create_batch_id(self) -> str:
        """A fresh ID for a multi-file run, unique among saved batches."""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        batch_id, suffix = f"{stamp}-batch", 1
        while self.batch_path(batch_id).exists():
            suffix += 1
            batch_id = f"{stamp}-batch-{suffix}"
        return batch_id

    def create_check_dir(self) -> Path:
        """A fresh directory for a `regress check` run's report and tool artifacts."""
        base = self.root / ".regress" / "checks"
        regress_dir(self.root)
        _check_artifact_path(self.root, base)
        base.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        directory, suffix = base / f"{stamp}-check", 1
        while True:
            try:
                directory.mkdir()
                return directory
            except FileExistsError:
                suffix += 1
                directory = base / f"{stamp}-check-{suffix}"

    def save_check(self, report: CheckReport, directory: Path) -> None:
        _check_artifact_path(self.root, directory)
        atomic_write_text(directory / CHECK_FILE, report.model_dump_json(indent=2))

    def batch_path(self, batch_id: str) -> Path:
        return self.root / ".regress" / "batches" / f"{batch_id}.json"

    def save_batch(self, report: BatchReport) -> None:
        """Save a multi-file run's summary next to the runs it grouped."""
        regress_dir(self.root)
        path = self.batch_path(report.id)
        _check_artifact_path(self.root, path.parent)
        path.parent.mkdir(exist_ok=True)
        atomic_write_text(path, report.model_dump_json(indent=2))

    def save(self, report: RunReport, run_dir: Path) -> None:
        _check_artifact_path(self.root, run_dir)
        atomic_write_text(run_dir / REPORT_FILE, report.model_dump_json(indent=2))

    def run_dirs(self) -> list[Path]:
        _check_artifact_path(self.root, self.base)
        if not self.base.is_dir():
            return []
        return sorted(
            p
            for p in self.base.iterdir()
            if p.is_dir() and not p.is_symlink() and (p / REPORT_FILE).is_file() and not (p / REPORT_FILE).is_symlink()
        )

    def load(self, run_id: str | None = None) -> tuple[RunReport, Path]:
        runs = self.run_dirs()
        if not runs:
            raise RegressError(f"No runs found in {self.base}. Start one with `regress run <file>`.")
        if run_id is None:
            run_dir = runs[-1]
        else:
            matches = [p for p in runs if p.name == run_id] or [p for p in runs if run_id in p.name]
            if not matches:
                raise RegressError(f"No run matching '{run_id}'. List runs with `regress report --list`.")
            run_dir = matches[-1]
        try:
            return RunReport.model_validate_json((run_dir / REPORT_FILE).read_text(encoding="utf-8")), run_dir
        except (OSError, ValueError) as error:
            raise RegressError(f"The report of run {run_dir.name} is unreadable: {error}") from error
