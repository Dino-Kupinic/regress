export type Interval = [start: number, end: number];

/** Orders an interval's endpoints so that start <= end. */
export function normalize([a, b]: Interval): Interval {
  return a <= b ? [a, b] : [b, a];
}

/** Merges overlapping intervals; touching intervals merge too unless mergeAdjacent is false. */
export function merge(intervals: Interval[], { mergeAdjacent = true } = {}): Interval[] {
  const sorted = intervals.map(normalize).sort((x, y) => x[0] - y[0] || x[1] - y[1]);
  const result: Interval[] = [];
  for (const [start, end] of sorted) {
    const last = result[result.length - 1];
    if (last && (start < last[1] || (mergeAdjacent && start === last[1]))) {
      last[1] = Math.max(last[1], end);
    } else {
      result.push([start, end]);
    }
  }
  return result;
}

/** Total length covered by the intervals, counting overlaps once. */
export function coveredLength(intervals: Interval[]): number {
  return merge(intervals).reduce((sum, [start, end]) => sum + (end - start), 0);
}

/** The parts of `within` not covered by any interval. */
export function gaps(intervals: Interval[], within: Interval): Interval[] {
  const [lo, hi] = normalize(within);
  const result: Interval[] = [];
  let cursor = lo;
  for (const [start, end] of merge(intervals)) {
    if (end <= cursor) continue;
    if (start >= hi) break;
    if (start > cursor) result.push([cursor, start]);
    cursor = Math.max(cursor, end);
  }
  if (cursor < hi) result.push([cursor, hi]);
  return result;
}
