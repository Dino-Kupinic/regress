from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from regress.errors import RegressError
from regress.models import RunReport

REPORT_FILE = "report.json"


def regress_dir(root: Path) -> Path:
    """The project's .regress directory, created on demand and ignored by git."""
    directory = root / ".regress"
    directory.mkdir(exist_ok=True)
    gitignore = directory / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("# Created by regress: run artifacts are local only.\n*\n")
    return directory


class RunStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.base = root / ".regress" / "runs"

    def create(self, source: Path) -> Path:
        regress_dir(self.root)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", source.stem).strip("-").lower() or "run"
        run_dir = self.base / f"{stamp}-{slug}"
        suffix = 1
        while run_dir.exists():
            suffix += 1
            run_dir = self.base / f"{stamp}-{slug}-{suffix}"
        run_dir.mkdir(parents=True)
        return run_dir

    def save(self, report: RunReport, run_dir: Path) -> None:
        (run_dir / REPORT_FILE).write_text(report.model_dump_json(indent=2))

    def run_dirs(self) -> list[Path]:
        if not self.base.is_dir():
            return []
        return sorted(p for p in self.base.iterdir() if (p / REPORT_FILE).is_file())

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
        return RunReport.model_validate_json((run_dir / REPORT_FILE).read_text()), run_dir
