export type Jitter = "none" | "full" | "equal";

export interface BackoffOptions {
  baseMs: number;
  factor: number;
  maxMs: number;
  jitter: Jitter;
}

export const DEFAULT_BACKOFF: BackoffOptions = { baseMs: 100, factor: 2, maxMs: 10_000, jitter: "none" };

/** Delay before retry number `attempt` (1-based), with optional jitter. */
export function backoffDelay(
  attempt: number,
  options: Partial<BackoffOptions> = {},
  random: () => number = Math.random,
): number {
  if (!Number.isInteger(attempt) || attempt < 1) {
    throw new RangeError("attempt must be a positive integer");
  }
  const { baseMs, factor, maxMs, jitter } = { ...DEFAULT_BACKOFF, ...options };
  const exponential = Math.min(maxMs, baseMs * factor ** (attempt - 1));
  switch (jitter) {
    case "none":
      return exponential;
    case "full":
      return Math.floor(random() * exponential);
    case "equal":
      return Math.floor(exponential / 2 + random() * (exponential / 2));
  }
}

export interface RetryOptions extends Partial<BackoffOptions> {
  retries?: number;
  shouldRetry?: (error: unknown, attempt: number) => boolean;
  sleep?: (ms: number) => Promise<void>;
  random?: () => number;
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Calls `fn` until it succeeds, retrying up to `retries` times with backoff between attempts. */
export async function retry<T>(fn: (attempt: number) => Promise<T>, options: RetryOptions = {}): Promise<T> {
  const { retries = 3, shouldRetry = () => true, sleep = defaultSleep, random = Math.random, ...backoff } = options;
  let attempt = 1;
  for (;;) {
    try {
      return await fn(attempt);
    } catch (error) {
      if (attempt > retries || !shouldRetry(error, attempt)) {
        throw error;
      }
      await sleep(backoffDelay(attempt, backoff, random));
      attempt++;
    }
  }
}
