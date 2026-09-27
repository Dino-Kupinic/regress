"""Candidate checks against Vitest's actual runtime skip reporting."""

import pytest
from conftest import EXAMPLES, requires_examples

from regress.evaluation import sandbox
from regress.project import load_project
from regress.validate import Workspace, check_candidate, static_problems
from regress.vitest import run_vitest

pytestmark = [pytest.mark.integration, requires_examples]

BASELINE = """\
import { expect, it } from "vitest";
import { Cart } from "../src/cart";
it("existing", () => expect(new Cart().itemCount).toBe(0));
it.skip("legacy", () => {});
"""
NEW_PASSING = 'it("new", () => expect(new Cart().subtotal()).toBe(0));\n'


@pytest.mark.parametrize(
    "candidate,accepted",
    [
        (
            BASELINE.replace(
                'it("existing", () => expect(new Cart().itemCount).toBe(0));',
                'it("existing", ({ skip }) => { skip(); });',
            )
            + NEW_PASSING,
            False,
        ),
        (BASELINE + 'it("new", ({ skip }) => { skip(); });\n', False),
        (BASELINE + 'it.concurrent.skip("new", () => {});\n', False),
        (BASELINE + NEW_PASSING, True),
    ],
    ids=["existing-runtime-skip", "new-runtime-skip", "chained-skip", "preserve-legacy-skip"],
)
def test_candidate_validation_uses_real_vitest_statuses(candidate, accepted):
    with sandbox(EXAMPLES) as root:
        project = load_project(root / "src/cart.ts")
        project.test_file.write_text(BASELINE)
        workspace = Workspace(project)
        output = root / ".regress" / "runtime-checks"
        previous = run_vitest(project, output, "baseline")
        assert previous.success and len(previous.skipped) == 1
        # All examples pass the static check, so rejection must come from runtime evidence.
        assert static_problems(project, candidate, BASELINE) == []
        try:
            check = check_candidate(
                project,
                workspace,
                candidate,
                previous=BASELINE,
                previous_result=previous,
                required_tests=previous.names,
                min_tests=previous.total + 1,
                output_dir=output,
                name="candidate",
            )
            assert check.result is not None and check.result.success
            assert check.ok is accepted, check.problems
        finally:
            workspace.restore()
