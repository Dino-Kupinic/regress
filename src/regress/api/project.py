"""The served project: whether it is ready, which files can be tested, and setting it up."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import HTTPException

from regress.api.jobs import RunManager
from regress.api.schemas import (
    CostEstimate,
    FileContent,
    FileEstimate,
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
from regress.estimates import estimate_project, run_options, total
from regress.files import atomic_write_text
from regress.pricing import PRICES_AS_OF, run_price
from regress.process import run_command
from regress.project import (
    MAX_FILE_BYTES,
    REQUIRED_PACKAGES,
    SKIP_DIRS,
    SOURCE_EXTENSIONS,
    default_test_path,
    detect_toolchain,
    find_test_file,
    import_specifier,
    install_command,
    is_test_file,
    load_project,
    package_version,
    planned_installs,
    source_files,
)
from regress.providers import key_env, missing_key
from regress.store import regress_dir

INSTALL_TIMEOUT = 600


def project_info(manager: RunManager) -> ProjectInfo:
    root = manager.root
    problems: list[str] = []
    if manager.blocking_problem is not None:
        problems.append(manager.blocking_problem)
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
    missing = missing_key(settings.provider, settings.api_key_env)
    api_key_set = missing is None
    if manager.needs_api_key and missing:
        problems.append(
            f"{missing.removesuffix(' Export it or add it to a .env file.')} "
            "Add it to the environment or a .env file and restart the server."
        )
    if settings.provider == "openai-compatible" and not (settings.base_url or os.environ.get("OPENAI_BASE_URL")):
        problems.append('provider = "openai-compatible" needs base_url in regress.toml (the server\'s API address).')
    if settings.model is None:
        problems.append(f"No model is set for provider {settings.provider}. Set model in regress.toml.")
    active = manager.active()
    batch = manager.active_batch()
    return ProjectInfo(
        root=str(root),
        name=_package_name(root),
        config_file=CONFIG_FILE if (root / CONFIG_FILE).is_file() else None,
        packages=[PackageStatus(name=name, version=package_version(root, name)) for name in REQUIRED_PACKAGES],
        toolchain=toolchain,
        problems=problems,
        ready=not problems,
        api_key_set=api_key_set,
        provider=settings.provider,
        api_key_name=key_env(settings.provider, settings.api_key_env),
        active_run=active.id if active else None,
        active_batch=batch.id if batch else None,
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
            atomic_write_text(config, CONFIG_TEMPLATE)
    return InitResult(installed=installs, command=command, config_written=written, project=project_info(manager))


def list_sources(root: Path) -> list[SourceFile]:
    """Files a run can target, with their sizes."""
    found = [SourceFile(path=p.relative_to(root).as_posix(), size=p.lstat().st_size) for p in source_files(root)]
    return sorted(found, key=lambda f: f.path)


def source_detail(manager: RunManager, relative: str) -> SourceDetail:
    source = readable_file(manager, relative)
    if is_test_file(source):
        raise ProjectError(f"{relative} is a test file. Pick the source file under test instead.")
    if not manager.same_project(source):
        raise ProjectError(f"{relative} belongs to a nested project with its own package.json.")
    test_file = find_test_file(manager.root, source) or default_test_path(manager.root, source)
    test_file = manager.project_path(test_file.relative_to(manager.root).as_posix())
    return SourceDetail(
        path=source.relative_to(manager.root).as_posix(),
        test_file=test_file.relative_to(manager.root).as_posix(),
        test_file_exists=test_file.is_file(),
        import_path=import_specifier(test_file, source),
        lines=len(_read_text(source).splitlines()),
    )


def read_file(manager: RunManager, relative: str) -> FileContent:
    path = readable_file(manager, relative)
    return FileContent(path=path.relative_to(manager.root).as_posix(), content=_read_text(path))


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(422, "The requested file is not valid UTF-8 text.") from error
    except FileNotFoundError as error:
        raise HTTPException(404, "The requested file no longer exists.") from error


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


def cost_estimate(
    manager: RunManager,
    sources: list[str],
    *,
    model: str | None = None,
    rounds: int | None = None,
    generate: bool = True,
    max_cost: float | None = None,
    tests: dict[str, str] | None = None,
) -> CostEstimate:
    """What runs on these files would cost with the configured (or given) model. Needs no API key."""
    settings = load_settings(manager.root, rounds=rounds, model=model, max_cost=max_cost)
    if settings.model is None:
        raise ProjectError(f"No model chosen for provider {settings.provider}: pass model, or set one in regress.toml.")
    price = run_price(settings)
    options = run_options(settings.model_copy(update={"max_cost": None}), settings.model, generate=generate)
    files = []
    for relative in dict.fromkeys(sources):
        test = (tests or {}).get(relative)
        project = load_project(
            manager.project_path(relative), manager.project_path(test) if test else None, settings.runner
        )
        if project.root != manager.root:
            raise ProjectError(f"{relative} is not in this project.")
        files.append(estimate_project(project, options, settings.model, price, settings.llm_max_output_tokens))
    summed = total(files)
    assert summed is not None
    return CostEstimate(
        model=settings.model,
        provider=settings.provider,
        price=price,
        prices_as_of=PRICES_AS_OF,
        max_cost=settings.max_cost,
        files=[
            FileEstimate(
                source_file=f.source_file,
                input_tokens=f.estimate.input_tokens,
                output_tokens=f.estimate.output_tokens,
                expected_usd=f.estimate.expected_usd,
                max_usd=f.estimate.max_usd,
            )
            for f in files
        ],
        calls=summed.calls,
        max_calls=summed.max_calls,
        input_tokens=summed.input_tokens,
        output_tokens=summed.output_tokens,
        expected_usd=summed.expected_usd,
        max_usd=summed.max_usd,
    )
