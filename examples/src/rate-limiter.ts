export interface Clock {
  now(): number;
}

const systemClock: Clock = { now: () => Date.now() };

/** A token bucket: holds up to `capacity` tokens and refills continuously. */
export class TokenBucket {
  private tokens: number;
  private lastRefill: number;

  constructor(
    private readonly capacity: number,
    private readonly refillPerSecond: number,
    private readonly clock: Clock = systemClock,
  ) {
    if (capacity <= 0) {
      throw new RangeError("capacity must be positive");
    }
    if (refillPerSecond < 0) {
      throw new RangeError("refillPerSecond cannot be negative");
    }
    this.tokens = capacity;
    this.lastRefill = clock.now();
  }

  private refill(): void {
    const now = this.clock.now();
    const elapsedSeconds = (now - this.lastRefill) / 1000;
    if (elapsedSeconds > 0) {
      this.tokens = Math.min(this.capacity, this.tokens + elapsedSeconds * this.refillPerSecond);
      this.lastRefill = now;
    }
  }

  tryRemove(count = 1): boolean {
    if (count <= 0) {
      throw new RangeError("count must be positive");
    }
    this.refill();
    if (this.tokens < count) {
      return false;
    }
    this.tokens -= count;
    return true;
  }

  available(): number {
    this.refill();
    return Math.floor(this.tokens);
  }

  msUntilAvailable(count = 1): number {
    this.refill();
    if (count > this.capacity) return Infinity;
    if (this.tokens >= count) return 0;
    if (this.refillPerSecond === 0) return Infinity;
    return Math.ceil(((count - this.tokens) / this.refillPerSecond) * 1000);
  }
}
