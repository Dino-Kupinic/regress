import { describe, expect, it } from "vitest";
import { TokenBucket } from "../src/rate-limiter";

describe("TokenBucket", () => {
  it("starts full", () => {
    const bucket = new TokenBucket(3, 1, { now: () => 0 });
    expect(bucket.available()).toBe(3);
  });

  it("allows removing a token", () => {
    const bucket = new TokenBucket(3, 1, { now: () => 0 });
    expect(bucket.tryRemove()).toBe(true);
  });
});
