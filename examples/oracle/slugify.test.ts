import { describe, expect, it } from "vitest";
import { slugify, truncate } from "../src/slugify";

describe("slugify", () => {
  it("lowercases and joins words", () => {
    expect(slugify("Hello World")).toBe("hello-world");
  });

  it("collapses punctuation and whitespace into one separator", () => {
    expect(slugify("  Hello,   World!! ")).toBe("hello-world");
  });

  it("strips diacritics", () => {
    expect(slugify("Crème brûlée")).toBe("creme-brulee");
  });

  it("spells out ampersands", () => {
    expect(slugify("Rock & Roll")).toBe("rock-and-roll");
  });

  it("keeps digits", () => {
    expect(slugify("Top 10 Tips")).toBe("top-10-tips");
  });

  it("returns an empty string for text without letters or digits", () => {
    expect(slugify("!!!")).toBe("");
  });

  it("uses a custom separator", () => {
    expect(slugify("Hello World", { separator: "_" })).toBe("hello_world");
  });

  it("trims a regex-special separator from both ends", () => {
    expect(slugify(" Hello World ", { separator: "." })).toBe("hello.world");
  });

  it("keeps a slug at exactly the maximum length", () => {
    expect(slugify("hello world", { maxLength: 11 })).toBe("hello-world");
  });

  it("cuts long slugs at the last separator", () => {
    expect(slugify("hello world again", { maxLength: 13 })).toBe("hello-world");
  });

  it("cuts mid-word when there is no separator to cut at", () => {
    expect(slugify("abcdefghij", { maxLength: 4 })).toBe("abcd");
  });

  it("defaults to a maximum length of 60", () => {
    expect(slugify("word ".repeat(30))).toBe(Array(12).fill("word").join("-"));
  });
});

describe("truncate", () => {
  it("leaves short text alone", () => {
    expect(truncate("short", 10)).toBe("short");
  });

  it("keeps text that is exactly the maximum length", () => {
    expect(truncate("hello", 5)).toBe("hello");
  });

  it("cuts at a word boundary in the second half", () => {
    expect(truncate("The quick brown fox", 12)).toBe("The quick…");
  });

  it("cuts mid-word when the last space is too early", () => {
    expect(truncate("a bcdefghijkl", 10)).toBe("a bcdefgh…");
  });

  it("leaves room for the ellipsis", () => {
    const result = truncate("abcdefghij", 5);
    expect(result).toBe("abcd…");
    expect(result).toHaveLength(5);
  });

  it("supports a custom ellipsis", () => {
    expect(truncate("abcdefghij", 6, "...")).toBe("abc...");
  });

  it("trims trailing spaces before the ellipsis", () => {
    expect(truncate("abcd    efgh", 7)).toBe("abcd…");
  });

  it("requires room for at least the ellipsis", () => {
    expect(() => truncate("abc", 2, "...")).toThrow("max must be at least the length of the ellipsis");
    expect(truncate("abcdef", 3, "...")).toBe("...");
  });
});
