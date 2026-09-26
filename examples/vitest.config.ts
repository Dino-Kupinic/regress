import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // oracle/*.oracle.ts are reference suites used to validate the hidden bugs; they must
    // never count towards a module's own tests.
    include: ["test/**/*.test.ts"],
  },
});
