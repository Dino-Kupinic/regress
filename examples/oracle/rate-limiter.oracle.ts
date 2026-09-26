import { describe, expect, it, vi } from "vitest";
import { TokenBucket } from "../src/rate-limiter";

function manualClock(start = 0) {
  let time = start;
  return {
    now: () => time,
    advance: (ms: number) => {
      time += ms;
    },
    set: (ms: number) => {
      time = ms;
    },
  };
}

describe("TokenBucket", () => {
  it("starts full", () => {
    const bucket = new TokenBucket(3, 1, { now: () => 0 });
    expect(bucket.available()).toBe(3);
  });

  it("allows removing a token", () => {
    const bucket = new TokenBucket(3, 1, { now: () => 0 });
    expect(bucket.tryRemove()).toBe(true);
  });

  it("rejects a non-positive capacity", () => {
    expect(() => new TokenBucket(0, 1)).toThrow("capacity must be positive");
    expect(() => new TokenBucket(-1, 1)).toThrow(RangeError);
  });

  it("rejects a negative refill rate but allows zero", () => {
    expect(() => new TokenBucket(1, -1)).toThrow("refillPerSecond cannot be negative");
    expect(() => new TokenBucket(1, 0)).not.toThrow();
  });

  it("denies removal once tokens run out", () => {
    const bucket = new TokenBucket(2, 1, manualClock());
    expect(bucket.tryRemove()).toBe(true);
    expect(bucket.tryRemove()).toBe(true);
    expect(bucket.tryRemove()).toBe(false);
    expect(bucket.available()).toBe(0);
  });

  it("removes several tokens at once, or none", () => {
    const bucket = new TokenBucket(5, 1, manualClock());
    expect(bucket.tryRemove(3)).toBe(true);
    expect(bucket.available()).toBe(2);
    expect(bucket.tryRemove(3)).toBe(false);
    expect(bucket.available()).toBe(2);
    expect(bucket.tryRemove(2)).toBe(true);
    expect(bucket.available()).toBe(0);
  });

  it("rejects non-positive counts", () => {
    const bucket = new TokenBucket(3, 1, manualClock());
    expect(() => bucket.tryRemove(0)).toThrow("count must be positive");
    expect(() => bucket.tryRemove(-2)).toThrow(RangeError);
  });

  it("refills over time at the configured rate", () => {
    const clock = manualClock();
    const bucket = new TokenBucket(4, 2, clock);
    bucket.tryRemove(4);
    clock.advance(500);
    expect(bucket.available()).toBe(1);
    clock.advance(500);
    expect(bucket.available()).toBe(2);
  });

  it("never refills beyond capacity", () => {
    const clock = manualClock();
    const bucket = new TokenBucket(3, 1, clock);
    clock.advance(10_000);
    expect(bucket.available()).toBe(3);
    expect(bucket.tryRemove(3)).toBe(true);
    expect(bucket.tryRemove(1)).toBe(false);
  });

  it("does not count elapsed time twice", () => {
    const clock = manualClock();
    const bucket = new TokenBucket(5, 1, clock);
    bucket.tryRemove(5);
    clock.advance(1000);
    expect(bucket.available()).toBe(1);
    expect(bucket.available()).toBe(1);
    expect(bucket.tryRemove()).toBe(true);
    expect(bucket.tryRemove()).toBe(false);
  });

  it("reports whole tokens but keeps fractions", () => {
    const clock = manualClock();
    const bucket = new TokenBucket(2, 1, clock);
    bucket.tryRemove(2);
    clock.advance(1500);
    expect(bucket.available()).toBe(1);
    expect(bucket.tryRemove()).toBe(true);
    expect(bucket.tryRemove()).toBe(false);
    clock.advance(500);
    expect(bucket.tryRemove()).toBe(true);
  });

  it("ignores a clock that goes backwards", () => {
    const clock = manualClock(1000);
    const bucket = new TokenBucket(2, 1, clock);
    bucket.tryRemove(2);
    clock.set(0);
    expect(bucket.available()).toBe(0);
  });

  it("uses the system clock by default", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(0);
      const bucket = new TokenBucket(1, 1);
      bucket.tryRemove();
      vi.setSystemTime(1000);
      expect(bucket.available()).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  describe("msUntilAvailable", () => {
    it("is zero when enough tokens are available", () => {
      const bucket = new TokenBucket(3, 1, manualClock());
      expect(bucket.msUntilAvailable()).toBe(0);
      expect(bucket.msUntilAvailable(3)).toBe(0);
    });

    it("is infinite for more tokens than the capacity", () => {
      const bucket = new TokenBucket(3, 1, manualClock());
      expect(bucket.msUntilAvailable(4)).toBe(Infinity);
    });

    it("is infinite when the bucket never refills", () => {
      const bucket = new TokenBucket(1, 0, manualClock());
      bucket.tryRemove();
      expect(bucket.msUntilAvailable()).toBe(Infinity);
    });

    it("computes the wait from the missing tokens", () => {
      const clock = manualClock();
      const bucket = new TokenBucket(4, 2, clock);
      bucket.tryRemove(4);
      clock.advance(500);
      expect(bucket.msUntilAvailable(3)).toBe(1000);
    });

    it("rounds the wait up to the next millisecond", () => {
      const bucket = new TokenBucket(1, 3, manualClock());
      bucket.tryRemove();
      expect(bucket.msUntilAvailable()).toBe(334);
    });
  });
});
