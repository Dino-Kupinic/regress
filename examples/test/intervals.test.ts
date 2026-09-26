import { describe, expect, it } from "vitest";
import { coveredLength, merge } from "../src/intervals";

describe("merge", () => {
  it("merges overlapping intervals", () => {
    expect(merge([[1, 3], [2, 5]])).toEqual([[1, 5]]);
  });

  it("keeps separate intervals apart", () => {
    expect(merge([[1, 2], [4, 5]])).toHaveLength(2);
  });
});

describe("coveredLength", () => {
  it("is zero for no intervals", () => {
    expect(coveredLength([])).toBe(0);
  });
});
