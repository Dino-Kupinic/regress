import { describe, expect, it, vi } from "vitest";
import { backoffDelay, retry } from "../src/backoff";

describe("backoffDelay", () => {
  it("starts at the base delay", () => {
    expect(backoffDelay(1)).toBe(100);
  });

  it("doubles the delay on each attempt", () => {
    expect([1, 2, 3, 4].map((attempt) => backoffDelay(attempt))).toEqual([100, 200, 400, 800]);
  });

  it("caps the delay at maxMs", () => {
    expect(backoffDelay(7)).toBe(6400);
    expect(backoffDelay(8)).toBe(10_000);
  });

  it("applies custom options over the defaults", () => {
    expect(backoffDelay(3, { baseMs: 50, factor: 3 })).toBe(450);
    expect(backoffDelay(2, { maxMs: 150 })).toBe(150);
  });

  it("applies full jitter between zero and the delay", () => {
    expect(backoffDelay(3, { jitter: "full" }, () => 0)).toBe(0);
    expect(backoffDelay(3, { jitter: "full" }, () => 0.5)).toBe(200);
    expect(backoffDelay(3, { jitter: "full" }, () => 0.999)).toBe(399);
  });

  it("applies equal jitter between half the delay and the delay", () => {
    expect(backoffDelay(3, { jitter: "equal" }, () => 0)).toBe(200);
    expect(backoffDelay(3, { jitter: "equal" }, () => 0.5)).toBe(300);
    expect(backoffDelay(1, { jitter: "equal" }, () => 0.999)).toBe(99);
  });

  it("rejects attempts that are not positive integers", () => {
    for (const attempt of [0, -1, 1.5, Number.NaN]) {
      expect(() => backoffDelay(attempt)).toThrow("attempt must be a positive integer");
    }
  });
});

describe("retry", () => {
  const noSleep = async () => {};

  it("returns the result of a successful call", async () => {
    await expect(retry(async () => 42)).resolves.toBe(42);
  });

  it("retries failures with growing delays until success", async () => {
    const sleeps: number[] = [];
    let calls = 0;
    const result = await retry(
      async (attempt) => {
        calls++;
        if (attempt < 3) throw new Error(`fail ${attempt}`);
        return "ok";
      },
      { sleep: async (ms) => void sleeps.push(ms) },
    );
    expect(result).toBe("ok");
    expect(calls).toBe(3);
    expect(sleeps).toEqual([100, 200]);
  });

  it("passes the attempt number to the function", async () => {
    const seen: number[] = [];
    await retry(
      async (attempt) => {
        seen.push(attempt);
        if (attempt < 2) throw new Error("again");
        return attempt;
      },
      { sleep: noSleep },
    );
    expect(seen).toEqual([1, 2]);
  });

  it("gives up after the configured number of retries", async () => {
    let calls = 0;
    const failing = async () => {
      calls++;
      throw new Error("boom");
    };
    await expect(retry(failing, { retries: 2, sleep: noSleep })).rejects.toThrow("boom");
    expect(calls).toBe(3);
  });

  it("retries three times by default", async () => {
    let calls = 0;
    const failing = async () => {
      calls++;
      throw new Error("boom");
    };
    await expect(retry(failing, { sleep: noSleep })).rejects.toThrow("boom");
    expect(calls).toBe(4);
  });

  it("does not retry when retries is zero", async () => {
    let calls = 0;
    const failing = async () => {
      calls++;
      throw new Error("boom");
    };
    await expect(retry(failing, { retries: 0, sleep: noSleep })).rejects.toThrow("boom");
    expect(calls).toBe(1);
  });

  it("stops as soon as shouldRetry returns false", async () => {
    const error = new Error("fatal");
    const decisions: Array<[unknown, number]> = [];
    let calls = 0;
    const failing = async () => {
      calls++;
      throw error;
    };
    const shouldRetry = (e: unknown, attempt: number) => {
      decisions.push([e, attempt]);
      return attempt < 2;
    };
    await expect(retry(failing, { sleep: noSleep, shouldRetry })).rejects.toBe(error);
    expect(calls).toBe(2);
    expect(decisions).toEqual([
      [error, 1],
      [error, 2],
    ]);
  });

  it("uses the backoff options and random source for its delays", async () => {
    const sleeps: number[] = [];
    const failing = async () => {
      throw new Error("x");
    };
    await expect(
      retry(failing, {
        retries: 2,
        baseMs: 10,
        factor: 3,
        jitter: "full",
        random: () => 0.5,
        sleep: async (ms) => void sleeps.push(ms),
      }),
    ).rejects.toThrow("x");
    expect(sleeps).toEqual([5, 15]);
  });

  it("waits with real timers by default", async () => {
    vi.useFakeTimers();
    try {
      let calls = 0;
      const promise = retry(async () => {
        calls++;
        if (calls === 1) throw new Error("once");
        return "done";
      });
      await vi.advanceTimersByTimeAsync(99);
      expect(calls).toBe(1);
      await vi.advanceTimersByTimeAsync(1);
      await expect(promise).resolves.toBe("done");
      expect(calls).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });
});
