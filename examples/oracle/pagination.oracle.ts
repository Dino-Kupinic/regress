import { describe, expect, it } from "vitest";
import { MAX_PAGE_SIZE, pageWindow, paginate } from "../src/pagination";

describe("paginate", () => {
  it("describes the first page", () => {
    expect(paginate(45, 1, 20)).toEqual({
      page: 1,
      pageSize: 20,
      totalItems: 45,
      totalPages: 3,
      offset: 0,
      hasPrevious: false,
      hasNext: true,
    });
  });

  it("describes a middle page", () => {
    expect(paginate(45, 2, 20)).toMatchObject({ page: 2, offset: 20, hasPrevious: true, hasNext: true });
  });

  it("describes the last page", () => {
    expect(paginate(45, 3, 20)).toMatchObject({ page: 3, offset: 40, hasPrevious: true, hasNext: false });
  });

  it("rounds the page count up", () => {
    expect(paginate(40, 1, 20).totalPages).toBe(2);
    expect(paginate(41, 1, 20).totalPages).toBe(3);
  });

  it("always has at least one page", () => {
    expect(paginate(0, 1)).toMatchObject({ totalPages: 1, page: 1, offset: 0, hasPrevious: false, hasNext: false });
  });

  it("defaults to 20 items per page", () => {
    expect(paginate(100, 2)).toMatchObject({ pageSize: 20, totalPages: 5, offset: 20 });
  });

  it("clamps pages below the first", () => {
    expect(paginate(45, 0, 20).page).toBe(1);
    expect(paginate(45, -3, 20).page).toBe(1);
  });

  it("clamps pages beyond the last", () => {
    expect(paginate(45, 9, 20)).toMatchObject({ page: 3, offset: 40, hasNext: false });
  });

  it("truncates fractional pages", () => {
    expect(paginate(45, 2.9, 20).page).toBe(2);
  });

  it("accepts page sizes from 1 to the maximum", () => {
    expect(MAX_PAGE_SIZE).toBe(100);
    expect(paginate(5, 1, 1).totalPages).toBe(5);
    expect(paginate(500, 1, 100).totalPages).toBe(5);
  });

  it("rejects invalid page sizes", () => {
    for (const size of [0, 101, 2.5]) {
      expect(() => paginate(10, 1, size)).toThrow("pageSize must be an integer between 1 and 100");
    }
  });

  it("rejects invalid item counts", () => {
    for (const total of [-1, 1.5]) {
      expect(() => paginate(total, 1)).toThrow("totalItems must be a non-negative integer");
    }
  });
});

describe("pageWindow", () => {
  it("centers the window on the current page", () => {
    expect(pageWindow(5, 10)).toEqual([3, 4, 5, 6, 7]);
  });

  it("starts at the first page near the beginning", () => {
    expect(pageWindow(1, 10)).toEqual([1, 2, 3, 4, 5]);
  });

  it("keeps a full window near the end", () => {
    expect(pageWindow(10, 10)).toEqual([6, 7, 8, 9, 10]);
  });

  it("shows every page when there are fewer than the window size", () => {
    expect(pageWindow(2, 3)).toEqual([1, 2, 3]);
  });

  it("supports custom window sizes", () => {
    expect(pageWindow(5, 10, 3)).toEqual([4, 5, 6]);
    expect(pageWindow(5, 10, 4)).toEqual([3, 4, 5, 6]);
  });
});
