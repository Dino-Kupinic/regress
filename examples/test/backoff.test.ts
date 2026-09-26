import { describe, expect, it } from "vitest";
import { backoffDelay, retry } from "../src/backoff";

describe("backoffDelay", () => {
  it("starts at the base delay", () => {
    expect(backoffDelay(1)).toBe(100);
  });
});

describe("retry", () => {
  it("returns the result of a successful call", async () => {
    await expect(retry(async () => 42)).resolves.toBe(42);
  });
});
