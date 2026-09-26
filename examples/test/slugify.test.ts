import { describe, expect, it } from "vitest";
import { slugify, truncate } from "../src/slugify";

describe("slugify", () => {
  it("lowercases and joins words", () => {
    expect(slugify("Hello World")).toBe("hello-world");
  });
});

describe("truncate", () => {
  it("leaves short text alone", () => {
    expect(truncate("short", 10)).toBe("short");
  });
});
