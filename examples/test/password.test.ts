import { describe, expect, it } from "vitest";
import { checkPassword, strength } from "../src/password";

describe("checkPassword", () => {
  it("accepts a strong password", () => {
    expect(checkPassword("Tr0ub4dor&Horse")).toEqual([]);
  });

  it("flags short passwords", () => {
    expect(checkPassword("Ab1!")).toContain("too_short");
  });
});

describe("strength", () => {
  it("rates a short lowercase password as weak", () => {
    expect(strength("abc")).toBe("weak");
  });
});
