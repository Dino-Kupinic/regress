from __future__ import annotations

import json
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from regress.errors import ProjectError

SOURCE_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
TEST_DIRS = ("test", "tests", "__tests__", "spec")
SKIP_DIRS = {"node_modules", ".git", ".regress", ".stryker-tmp", "dist", "build", "coverage", "reports"}
MAX_FILE_BYTES = 1_000_000
_CONFIG_NAME = re.compile(r"\.(config|conf)\.[cm]?[jt]sx?$|\.d\.[cm]?ts$")

REQUIRED_PACKAGES = ("vitest", "@stryker-mutator/core", "@stryker-mutator/vitest-runner")
# Stryker's vitest runner (<= 10.x) never activates mutants under Vitest 5, so every mutant "survives".
VITEST_INSTALL_SPEC = "vitest@^4"

Runner = Literal["auto", "bun", "npx"]

_IMPORT_RE = re.compile(
    r"""(?:import|export)\s[^'"]*?from\s*['"]([^'"]+)['"]"""
    r"""|import\s*\(\s*['"]([^'"]+)['"]\s*\)"""
    r"""|import\s+['"]([^'"]+)['"]"""
    r"""|require\(\s*['"]([^'"]+)['"]\s*\)"""
)
_MOCK_RE = re.compile(r"""vi\.(?:do)?[mM]ock\(\s*['"]([^'"]+)['"]""")


@dataclass(frozen=True)
class Toolchain:
    runner: Literal["bun", "npx"]
    vitest: str
    stryker: str
    vitest_runner: str

    def command(self, tool: str, *args: str) -> list[str]:
        if self.runner == "bun":
            return ["bun", "x", tool, *args]
        return ["npx", "--no-install", tool, *args]

    def describe(self) -> str:
        return f"{self.runner} · vitest {self.vitest} · stryker {self.stryker}"


@dataclass(frozen=True)
class Project:
    root: Path
    source: Path
    test_file: Path
    toolchain: Toolchain

    @property
    def source_rel(self) -> str:
        return self.source.relative_to(self.root).as_posix()

    @property
    def test_rel(self) -> str:
        return self.test_file.relative_to(self.root).as_posix()

    @property
    def import_path(self) -> str:
        """How the test file should import the module under test."""
        return import_specifier(self.test_file, self.source)


def find_project_root(start: Path) -> Path:
    for directory in [start, *start.parents]:
        if (directory / "package.json").is_file():
            return directory
    raise ProjectError(f"No package.json found in {start} or any parent directory.")


def package_version(root: Path, name: str) -> str | None:
    """Version of an installed npm package, looking in hoisted node_modules too."""
    for directory in [root, *root.parents]:
        manifest = directory / "node_modules" / name / "package.json"
        if manifest.is_file():
            try:
                return str(json.loads(manifest.read_text())["version"])
            except (OSError, ValueError, KeyError, TypeError):
                return None
    return None


def major(version: str) -> int:
    match = re.match(r"\d+", version)
    return int(match.group()) if match else 0


def pick_runner(preference: Runner) -> Literal["bun", "npx"]:
    if preference == "bun" or (preference == "auto" and shutil.which("bun")):
        if not shutil.which("bun"):
            raise ProjectError("Bun is not installed. Install it from https://bun.sh or use --runner npx.")
        return "bun"
    if not shutil.which("npx"):
        raise ProjectError("Neither bun nor npx is available on PATH.")
    return "npx"


def missing_packages(root: Path) -> list[str]:
    return [name for name in REQUIRED_PACKAGES if package_version(root, name) is None]


def detect_toolchain(root: Path, runner: Runner = "auto") -> Toolchain:
    missing = missing_packages(root)
    if missing:
        raise ProjectError(
            f"Missing dev dependencies: {', '.join(missing)}.\n"
            f"Run `regress init` to install them, or: bun add -d {' '.join(install_specs(missing))}"
        )
    vitest = package_version(root, "vitest") or "?"
    vitest_runner = package_version(root, "@stryker-mutator/vitest-runner") or "?"
    if major(vitest) >= 5 and major(vitest_runner) <= 10:
        raise ProjectError(
            f"vitest {vitest} is not supported by @stryker-mutator/vitest-runner {vitest_runner}: "
            "mutants are never activated, so every mutant would falsely survive.\n"
            f"Install Vitest 4 instead: bun add -d {VITEST_INSTALL_SPEC}"
        )
    if major(vitest) < 2:
        raise ProjectError(f"vitest {vitest} is too old for Stryker; install {VITEST_INSTALL_SPEC}.")
    return Toolchain(
        runner=pick_runner(runner),
        vitest=vitest,
        stryker=package_version(root, "@stryker-mutator/core") or "?",
        vitest_runner=vitest_runner,
    )


def install_specs(packages: list[str]) -> list[str]:
    return [VITEST_INSTALL_SPEC if name == "vitest" else name for name in packages]


def planned_installs(root: Path) -> list[str]:
    """What `regress init` installs: missing tools, and Vitest 4 in place of an unsupported Vitest 5."""
    installs = install_specs(missing_packages(root))
    vitest = package_version(root, "vitest")
    if vitest and major(vitest) >= 5:
        installs.append(VITEST_INSTALL_SPEC)
    return installs


def install_command(root: Path, packages: list[str]) -> list[str]:
    """A dev-dependency install with the package manager the project's lockfile points to."""
    lockfiles = {
        "bun.lock": ["bun", "add", "-d"],
        "bun.lockb": ["bun", "add", "-d"],
        "pnpm-lock.yaml": ["pnpm", "add", "-D"],
        "yarn.lock": ["yarn", "add", "-D"],
        "package-lock.json": ["npm", "install", "-D"],
    }
    for lockfile, command in lockfiles.items():
        if (root / lockfile).exists():
            return [*command, *packages]
    return ["bun", "add", "-d", *packages] if shutil.which("bun") else ["npm", "install", "-D", *packages]


def load_project(source: Path, test_file: Path | None = None, runner: Runner = "auto") -> Project:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise ProjectError(f"Source file not found: {source}")
    if source.suffix not in SOURCE_EXTENSIONS:
        raise ProjectError(
            f"Unsupported source file type '{source.suffix}'. Expected one of {', '.join(SOURCE_EXTENSIONS)}."
        )
    if is_test_file(source):
        raise ProjectError(f"{source.name} looks like a test file. Pass the source file under test instead.")
    root = find_project_root(source.parent)
    toolchain = detect_toolchain(root, runner)

    if test_file is not None:
        test_file = test_file.expanduser().resolve()
        if not test_file.is_relative_to(root):
            raise ProjectError(f"Test file {test_file} is outside the project at {root}.")
    else:
        test_file = find_test_file(root, source) or default_test_path(root, source)
    test_file = test_file.resolve()
    if not test_file.is_relative_to(root):
        raise ProjectError(f"Test file {test_file} is outside the project at {root}.")
    if test_file.suffix not in SOURCE_EXTENSIONS:
        raise ProjectError("The test file must be a JavaScript or TypeScript file.")
    if test_file.exists() and not test_file.is_file():
        raise ProjectError(f"Test file {test_file} is not a regular file.")
    if test_file == source:
        raise ProjectError("The test file and the source file must be different files.")
    return Project(root=root, source=source, test_file=test_file, toolchain=toolchain)


def is_test_file(path: Path) -> bool:
    return bool(re.search(r"\.(test|spec)\.[cm]?[jt]sx?$", path.name))


def _test_file_names(source: Path) -> list[str]:
    extensions = [source.suffix, *(e for e in SOURCE_EXTENSIONS if e != source.suffix)]
    return [f"{source.stem}.{kind}{ext}" for ext in extensions for kind in ("test", "spec")]


def _mirror_dir(root: Path, source: Path) -> Path:
    """Source directory relative to the root, without a leading src/ or lib/."""
    parts = source.parent.relative_to(root).parts
    return Path(*parts[1:]) if parts and parts[0] in ("src", "lib") else Path(*parts)


def find_test_file(root: Path, source: Path) -> Path | None:
    names = _test_file_names(source)
    relative_dir = source.parent.relative_to(root)
    mirror = _mirror_dir(root, source)
    directories = [source.parent, source.parent / "__tests__"]
    for test_dir in TEST_DIRS:
        directories += [root / test_dir / mirror, root / test_dir / relative_dir, root / test_dir]
    for directory in directories:
        for name in names:
            if (directory / name).is_file():
                return directory / name

    # Anywhere else, a same-named file only counts if it actually imports the module under test.
    matches = sorted(path for path in walk_files(root) if path.name in names and _imports(path, source))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        listing = "\n".join(f"  {m.relative_to(root)}" for m in matches)
        raise ProjectError(f"Several candidate test files found; pick one with --test:\n{listing}")
    return None


def _imports(test_file: Path, source: Path) -> bool:
    try:
        code = test_file.read_text()
    except (OSError, UnicodeDecodeError):
        return False
    return any(refers_to(test_file, specifier, source) for specifier in import_specifiers(code))


def default_test_path(root: Path, source: Path) -> Path:
    name = f"{source.stem}.test{source.suffix}"
    for test_dir in ("test", "tests"):
        if (root / test_dir).is_dir():
            return root / test_dir / _mirror_dir(root, source) / name
    return source.parent / name


def source_files(root: Path, under: Path | None = None) -> list[Path]:
    """Files a run can target: JavaScript and TypeScript sources, without tests, configs, or type declarations.

    Each one is a regular file that is not hidden, not too large, and belongs to this project rather than a
    nested one with its own package.json. `under` limits the search to one directory of the project.
    """
    found = []
    for directory, dirnames, filenames in os.walk(under or root):
        here = Path(directory)
        if here != root and "package.json" in filenames and (here / "package.json").is_file():
            dirnames.clear()  # a nested project: none of its files are this project's sources
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d not in TEST_DIRS and not d.startswith(".")]
        for name in filenames:
            path = here / name
            if (
                name.startswith(".")
                or path.suffix not in SOURCE_EXTENSIONS
                or is_test_file(path)
                or ".oracle." in name
                or _CONFIG_NAME.search(name)
            ):
                continue
            try:
                info = path.lstat()  # lstat, so a symlink is never taken for the file it points to
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode) and info.st_size <= MAX_FILE_BYTES:
                found.append(path)
    return sorted(found)


def walk_files(root: Path):
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for filename in filenames:
            yield Path(directory) / filename


def import_specifiers(code: str) -> list[str]:
    return [next(group for group in match.groups() if group) for match in _IMPORT_RE.finditer(code)]


def mock_specifiers(code: str) -> list[str]:
    return _MOCK_RE.findall(code)


def resolve_import(from_file: Path, specifier: str) -> Path | None:
    if not specifier.startswith("."):
        return None
    base = (from_file.parent / specifier).resolve()
    candidates = [base]
    if base.suffix in (".js", ".jsx", ".mjs", ".cjs"):
        # TypeScript ESM style: `import "./cart.js"` refers to cart.ts.
        stem = base.with_suffix("")
        candidates += [stem.with_name(stem.name + ext) for ext in (".ts", ".tsx", ".mts", ".cts")]
    candidates += [base.with_name(base.name + ext) for ext in SOURCE_EXTENSIONS]
    candidates += [base / f"index{ext}" for ext in SOURCE_EXTENSIONS]
    return next((c for c in candidates if c.is_file()), None)


def refers_to(from_file: Path, specifier: str, target: Path) -> bool:
    """Whether an import specifier points at the target file (path aliases matched by file stem)."""
    if specifier.startswith("."):
        return resolve_import(from_file, specifier) == target.resolve()
    last = specifier.rstrip("/").rsplit("/", 1)[-1]
    return not specifier.startswith(("node:", "vitest")) and "/" in specifier and last.split(".")[0] == target.stem


def import_specifier(from_file: Path, target: Path) -> str:
    relative = os.path.relpath(target.with_suffix(""), from_file.parent).replace(os.sep, "/")
    return relative if relative.startswith(".") else f"./{relative}"


def related_files(source: Path, root: Path, max_files: int = 5) -> list[Path]:
    """Local modules the source imports directly: types and helpers the model needs to see."""
    found: list[Path] = []
    for specifier in import_specifiers(source.read_text()):
        path = resolve_import(source, specifier)
        if path and path != source and path.is_relative_to(root) and path not in found:
            found.append(path)
    return found[:max_files]
