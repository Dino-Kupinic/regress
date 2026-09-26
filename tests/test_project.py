from pathlib import Path

import pytest
from conftest import make_node_package

from regress.errors import ProjectError
from regress.project import (
    default_test_path,
    detect_toolchain,
    find_test_file,
    import_specifier,
    import_specifiers,
    load_project,
    refers_to,
    related_files,
    resolve_import,
)


def write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_finds_colocated_test_file(js_project):
    source = write(js_project / "src/cart.ts")
    test = write(js_project / "src/cart.test.ts")
    assert find_test_file(js_project, source) == test


def test_finds_mirrored_test_file_without_src_prefix(js_project):
    source = write(js_project / "src/shop/cart.ts")
    test = write(js_project / "test/shop/cart.spec.ts")
    assert find_test_file(js_project, source) == test


def test_finds_test_file_anywhere_as_a_fallback(js_project):
    source = write(js_project / "src/cart.ts")
    test = write(js_project / "specs/unit/cart.test.ts", 'import { Cart } from "../../src/cart";\n')
    assert find_test_file(js_project, source) == test


def test_fallback_ignores_same_named_tests_of_other_modules(js_project):
    source = write(js_project / "src/cart.ts")
    write(js_project / "api/cart.ts")
    write(js_project / "api/tests/cart.test.ts", 'import { handler } from "../cart";\n')
    assert find_test_file(js_project, source) is None


def test_ambiguous_test_files_need_an_explicit_choice(js_project):
    source = write(js_project / "src/cart.ts")
    write(js_project / "a/cart.test.ts", 'import "../src/cart";\n')
    write(js_project / "b/cart.test.ts", 'import "../src/cart";\n')
    with pytest.raises(ProjectError, match="--test"):
        find_test_file(js_project, source)


def test_ignores_node_modules_when_searching(js_project):
    source = write(js_project / "src/cart.ts")
    write(js_project / "node_modules/pkg/cart.test.ts", 'import "../../src/cart";\n')
    assert find_test_file(js_project, source) is None


def test_new_test_files_go_to_an_existing_test_dir(js_project):
    source = write(js_project / "src/shop/cart.ts")
    (js_project / "test").mkdir()
    assert default_test_path(js_project, source) == js_project / "test/shop/cart.test.ts"


def test_new_test_files_are_colocated_without_a_test_dir(js_project):
    source = write(js_project / "src/cart.tsx")
    assert default_test_path(js_project, source) == js_project / "src/cart.test.tsx"


def test_load_project_rejects_test_files_and_unknown_types(js_project):
    with pytest.raises(ProjectError, match="looks like a test file"):
        load_project(write(js_project / "src/cart.test.ts"))
    with pytest.raises(ProjectError, match="Unsupported"):
        load_project(write(js_project / "src/cart.py"))


def test_load_project_resolves_root_and_test_file(js_project):
    source = write(js_project / "src/cart.ts")
    project = load_project(source, runner="npx")
    assert project.root == js_project.resolve()
    assert project.test_rel == "src/cart.test.ts"
    assert project.import_path == "./cart"


def test_missing_packages_are_reported_with_install_hint(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    make_node_package(tmp_path, "vitest", "4.1.0")
    with pytest.raises(ProjectError, match="@stryker-mutator/core") as error:
        detect_toolchain(tmp_path)
    assert "regress init" in str(error.value)


def test_vitest_5_is_rejected_because_mutants_never_activate(js_project):
    (js_project / "node_modules/vitest/package.json").write_text('{"version": "5.0.2"}')
    with pytest.raises(ProjectError, match="vitest@\\^4"):
        detect_toolchain(js_project)


def test_toolchain_commands_by_runner(js_project):
    assert detect_toolchain(js_project, "npx").command("vitest", "run") == ["npx", "--no-install", "vitest", "run"]


def test_import_specifiers_cover_common_forms():
    code = """
    import { a } from "./a";
    import type { B } from '../b.js';
    export * from "./c";
    import "./side-effect";
    const d = await import("./d");
    const e = require("./e");
    import {
      f,
    } from "./f";
    """
    assert import_specifiers(code) == ["./a", "../b.js", "./c", "./side-effect", "./d", "./e", "./f"]


def test_resolves_extensionless_ts_esm_and_index_imports(tmp_path):
    source = write(tmp_path / "src/cart.ts")
    types = write(tmp_path / "src/types.ts")
    index = write(tmp_path / "src/util/index.ts")
    assert resolve_import(source, "./types") == types.resolve()
    assert resolve_import(source, "./types.js") == types.resolve()
    assert resolve_import(source, "./util") == index.resolve()
    assert resolve_import(source, "lodash") is None


def test_refers_to_matches_relative_and_aliased_imports(tmp_path):
    source = write(tmp_path / "src/cart.ts")
    test = write(tmp_path / "test/cart.test.ts")
    assert refers_to(test, "../src/cart", source)
    assert refers_to(test, "@/cart", source)
    assert not refers_to(test, "../src/other", source)
    assert not refers_to(test, "vitest", source)


def test_import_specifier_from_test_to_source(tmp_path):
    assert import_specifier(tmp_path / "test/cart.test.ts", tmp_path / "src/cart.ts") == "../src/cart"
    assert import_specifier(tmp_path / "src/cart.test.ts", tmp_path / "src/cart.ts") == "./cart"


def test_related_files_are_local_imports_only(tmp_path):
    types = write(tmp_path / "src/types.ts")
    source = write(tmp_path / "src/cart.ts", 'import { T } from "./types";\nimport z from "zod";\n')
    assert related_files(source, tmp_path) == [types.resolve()]
