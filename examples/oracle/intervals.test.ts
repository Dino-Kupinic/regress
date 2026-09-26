import { describe, expect, it } from "vitest";
import { coveredLength, gaps, type Interval, merge, normalize } from "../src/intervals";

describe("merge", () => {
  it("merges overlapping intervals", () => {
    expect(merge([[1, 3], [2, 5]])).toEqual([[1, 5]]);
  });

  it("keeps separate intervals apart", () => {
    expect(merge([[1, 2], [4, 5]])).toHaveLength(2);
  });

  it("returns an empty list for no intervals", () => {
    expect(merge([])).toEqual([]);
  });

  it("sorts intervals by start", () => {
    expect(merge([[5, 6], [1, 2]])).toEqual([[1, 2], [5, 6]]);
  });

  it("keeps a contained interval inside its container", () => {
    expect(merge([[1, 10], [2, 3]])).toEqual([[1, 10]]);
  });

  it("normalizes reversed intervals", () => {
    expect(merge([[5, 1]])).toEqual([[1, 5]]);
  });

  it("merges touching intervals by default", () => {
    expect(merge([[1, 2], [2, 3]])).toEqual([[1, 3]]);
  });

  it("keeps touching intervals apart when mergeAdjacent is false", () => {
    expect(merge([[1, 2], [2, 3]], { mergeAdjacent: false })).toEqual([[1, 2], [2, 3]]);
  });

  it("still merges overlaps when mergeAdjacent is false", () => {
    expect(merge([[1, 3], [2, 4]], { mergeAdjacent: false })).toEqual([[1, 4]]);
  });

  it("merges chains of intervals", () => {
    expect(merge([[1, 3], [2, 6], [5, 8], [10, 12]])).toEqual([[1, 8], [10, 12]]);
  });

  it("does not mutate its input", () => {
    const input: Interval[] = [[1, 3], [2, 5]];
    merge(input);
    expect(input).toEqual([[1, 3], [2, 5]]);
  });
});

describe("normalize", () => {
  it("orders endpoints", () => {
    expect(normalize([3, 1])).toEqual([1, 3]);
    expect(normalize([1, 3])).toEqual([1, 3]);
    expect(normalize([2, 2])).toEqual([2, 2]);
  });
});

describe("coveredLength", () => {
  it("is zero for no intervals", () => {
    expect(coveredLength([])).toBe(0);
  });

  it("counts overlapping parts once", () => {
    expect(coveredLength([[0, 5], [3, 8], [10, 11]])).toBe(9);
  });

  it("measures reversed intervals", () => {
    expect(coveredLength([[4, 1]])).toBe(3);
  });
});

describe("gaps", () => {
  it("finds the gaps between intervals", () => {
    expect(gaps([[2, 3], [5, 7]], [0, 10])).toEqual([[0, 2], [3, 5], [7, 10]]);
  });

  it("returns the whole window when nothing overlaps it", () => {
    expect(gaps([], [0, 4])).toEqual([[0, 4]]);
    expect(gaps([[-5, -1], [20, 30]], [0, 10])).toEqual([[0, 10]]);
  });

  it("clips intervals that straddle the window edges", () => {
    expect(gaps([[-2, 1], [9, 12]], [0, 10])).toEqual([[1, 9]]);
  });

  it("returns nothing when the window is fully covered", () => {
    expect(gaps([[0, 10]], [2, 8])).toEqual([]);
  });

  it("accepts a reversed window", () => {
    expect(gaps([[2, 3]], [10, 0])).toEqual([[0, 2], [3, 10]]);
  });

  it("has no gap where intervals touch", () => {
    expect(gaps([[0, 2], [2, 4]], [0, 4])).toEqual([]);
  });

  it("ignores intervals that end at the window start or begin at its end", () => {
    expect(gaps([[-3, 0]], [0, 5])).toEqual([[0, 5]]);
    expect(gaps([[5, 8]], [0, 5])).toEqual([[0, 5]]);
  });
});
