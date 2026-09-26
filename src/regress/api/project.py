"""The served project: whether it is ready, which files can be tested, and setting it up."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from fastapi import HTTPException

from regress.api.jobs import RunManager
from regress.api.schemas import (
    FileContent,
    InitResult,
    PackageStatus,
    ProjectInfo,
    SettingsInfo,
    SourceDetail,
    SourceFile,
    Toolchain,
)
from regress.config import CONFIG_FILE, CONFIG_TEMPLATE, Settings, load_settings, user_config_path, user_settings
from regress.errors import ProjectError, ToolError
from regress.process import run_command
from regress.project import (
    REQUIRED_PACKAGES,
    SKIP_DIRS,
    SOURCE_EXTENSIONS,
    TEST_DIRS,
    default_test_path,
    detect_toolchain,
    find_test_file,
    import_specifier,
    install_command,
    is_test_file,
    package_version,
    planned_installs,
    walk_files,
)
from regress.store import regress_dir

MAX_FILE_BYTES = 1_000_000
INSTALL_TIMEOUT = 600
_CONFIG_NAME = re.compile(r"\.(config|conf)\.[cm]?[jt]sx?$|\.d\.[cm]?ts$")


def project_info(manager: RunManager) -> ProjectInfo:
    root = manager.root
    problems: list[str] = []
    try:
        settings = load_settings(root)
    except ProjectError as error:
        problems.append(str(error))
        settings = Settings()
    toolchain = None
    try:
        found = detect_toolchain(root, settings.runner)
        toolchain = Toolchain(
            runner=found.runner, vitest=found.vitest, stryker=found.stryker, vitest_runner=found.vitest_runner
        )
    except ProjectError as error:
        problems.append(str(error))
    api_key_set = bool(os.environ.get("OPENAI_API_KEY"))
    if manager.needs_api_key and not api_key_set:
        problems.append("OPENAI_API_KEY is not set. Add it to the environment or a .env file and restart the server.")
    active = manager.active()
    return ProjectInfo(
        root=str(root),
        name=_package_name(root),
        config_file=CONFIG_FILE if (root / CONFIG_FILE).is_file() else None,
        packages=[PackageStatus(name=name, version=package_version(root, name)) for name in REQUIRED_PACKAGES],
        toolchain=toolchain,
        problems=problems,
        ready=not problems,
        api_key_set=api_key_set,
        active_run=active.id if active else None,
    )


def init_project(manager: RunManager) -> InitResult:
    """What `regress init --yes` does: install missing packages and write regress.toml."""
    root = manager.root
    with manager.exclusive("installing dependencies"):
        installs = planned_installs(root)
        command = install_command(root, installs) if installs else None
        if command:
            try:
                result = run_command(command, cwd=root, timeout=INSTALL_TIMEOUT)
            except FileNotFoundError as error:
                raise ToolError(f"Cannot install dependencies: {command[0]} is not installed.") from error
            if result.returncode != 0:
                raise ToolError("Installing dependencies failed.", result.output[-3000:])
        regress_dir(root)
        config = root / CONFIG_FILE
        written = not config.exists()
        if written:
            config.write_text(CONFIG_TEMPLATE)
    return InitResult(installed=installs, command=command, config_written=written, project=project_info(manager))


def list_sources(root: Path) -> list[SourceFile]:
    """Files a run can target: JavaScript and TypeScript sources, without tests, configs, or type declarations."""
    found = []
    for path in walk_files(root):
        relative = path.relative_to(root)
        if (
            path.suffix not in SOURCE_EXTENSIONS
            or is_test_file(path)
            or ".oracle." in path.name
            or _CONFIG_NAME.search(path.name)
            or any(part in TEST_DIRS for part in relative.parts[:-1])
        ):
            continue
        found.append(SourceFile(path=relative.as_posix(), size=path.stat().st_size))
    return sorted(found, key=lambda f: f.path)


def source_detail(manager: RunManager, relative: str) -> SourceDetail:
    source = readable_file(manager, relative)
    if is_test_file(source):
        raise ProjectError(f"{relative} is a test file. Pick the source file under test instead.")
    if not manager.same_project(source):
        raise ProjectError(f"{relative} belongs to a nested project with its own package.json.")
    test_file = find_test_file(manager.root, source) or default_test_path(manager.root, source)
    return SourceDetail(
        path=source.relative_to(manager.root).as_posix(),
        test_file=test_file.relative_to(manager.root).as_posix(),
        test_file_exists=test_file.is_file(),
        import_path=import_specifier(test_file, source),
        lines=len(source.read_text().splitlines()),
    )


def read_file(manager: RunManager, relative: str) -> FileContent:
    path = readable_file(manager, relative)
    return FileContent(path=path.relative_to(manager.root).as_posix(), content=path.read_text())


def readable_file(manager: RunManager, relative: str) -> Path:
    """A source or test file the browser may read. Only JS/TS files outside hidden and dependency folders
    qualify, so nothing like .env or node_modules is ever served."""
    try:
        path = manager.project_path(relative)
    except ProjectError as error:
        raise HTTPException(403, str(error)) from error
    parts = path.relative_to(manager.root).parts
    if not parts or path.suffix not in SOURCE_EXTENSIONS or any(p in SKIP_DIRS or p.startswith(".") for p in parts):
        raise HTTPException(403, "Only JavaScript and TypeScript files of the project can be read.")
    if not path.is_file():
        raise HTTPException(404, f"No file {relative}.")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise HTTPException(413, f"{relative} is larger than {MAX_FILE_BYTES // 1000} kB.")
    return path


def settings_info(root: Path) -> SettingsInfo:
    settings = load_settings(root)
    project_config = root / CONFIG_FILE
    return SettingsInfo(
        effective=settings,
        model_source=settings.model_source,
        user_config_path=str(user_config_path()),
        user_config=user_settings(),
        project_config_path=str(project_config) if project_config.is_file() else None,
    )


def _package_name(root: Path) -> str:
    try:
        name = json.loads((root / "package.json").read_text()).get("name")
    except (OSError, ValueError, AttributeError):
        name = None
    return name if isinstance(name, str) and name else root.name
