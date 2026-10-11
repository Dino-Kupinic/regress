// Stryker 10's Vitest runner reports test files but drops task locations. Without
// locations every edit invalidates every test, preventing reuse between rounds.
// Keep its runner and lifecycle, adding only locations collected by Vitest.
import path from "node:path";
import { strykerPlugins as upstream, strykerValidationSchema } from "@stryker-mutator/vitest-runner";

export { strykerValidationSchema };
const plugin = upstream.find((plugin) => plugin.name === "vitest");

function createRunner(...args) {
  const runner = plugin.factory(...args);
  const init = runner.init.bind(runner);
  runner.init = async () => {
    await init();
    if (runner.ctx) {
      runner.ctx.config.includeTaskLocation = true;
      for (const project of runner.ctx.projects) project.config.includeTaskLocation = true;
    }
  };
  const dryRun = runner.dryRun.bind(runner);
  runner.dryRun = async (options) => {
    const result = await dryRun(options);
    if (result.status !== "complete" || !runner.ctx) return result;
    const locations = new Map();
    function visit(task) {
      if (task.type === "suite") {
        for (const child of task.tasks) visit(child);
      } else if (task.type === "test" && task.location && task.file?.filepath) {
        const names = [task.name];
        for (let suite = task.suite; suite; suite = suite.suite) names.unshift(suite.name);
        const key = `${path.resolve(task.file.filepath)}#${names.join(" ").trim()}`;
        // Duplicate/parameterized names with ambiguous locations fall back to the
        // upstream whole-file invalidation rather than guessing.
        locations.set(key, locations.has(key) ? null : task.location);
      }
    }
    for (const file of runner.ctx.state.getFiles()) visit(file);
    for (const test of result.tests) {
      const location = test.fileName && locations.get(`${path.resolve(test.fileName)}#${test.name}`);
      if (!test.startPosition && location) {
        test.startPosition = { line: location.line - 1, column: location.column - 1 };
      }
    }
    return result;
  };
  return runner;
}
createRunner.inject = plugin.factory.inject;
export const strykerPlugins = [{ ...plugin, factory: createRunner }];
