import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // oracle/ holds the reference suites used to validate hidden bugs; they must never
    // count towards a module's own tests.
    include: ["test/**/*.test.ts"],
  },
});
