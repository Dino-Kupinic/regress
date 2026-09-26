import { describe, expect, it } from "vitest";
import { checkPassword, isValid, strength } from "../src/password";

describe("checkPassword", () => {
  it("accepts a strong password", () => {
    expect(checkPassword("Tr0ub4dor&Horse")).toEqual([]);
  });

  it("flags short passwords", () => {
    expect(checkPassword("Ab1!")).toContain("too_short");
  });

  it("reports every violation in order", () => {
    expect(checkPassword("aaa")).toEqual([
      "too_short",
      "missing_digit",
      "missing_symbol",
      "missing_mixed_case",
      "repeated_characters",
    ]);
  });

  it("accepts exactly the minimum length", () => {
    expect(checkPassword("Abcdefgh12!x")).toEqual([]);
  });

  it("rejects one character below the minimum length", () => {
    expect(checkPassword("Abcdefgh12!")).toEqual(["too_short"]);
  });

  it("requires a digit", () => {
    expect(checkPassword("Abcdefghij!k")).toEqual(["missing_digit"]);
  });

  it("requires a symbol", () => {
    expect(checkPassword("Abcdefghij12")).toEqual(["missing_symbol"]);
  });

  it("counts an underscore as a symbol", () => {
    expect(checkPassword("Abcdefgh123_")).toEqual([]);
  });

  it("requires both upper and lower case", () => {
    expect(checkPassword("abcdefgh12!x")).toEqual(["missing_mixed_case"]);
    expect(checkPassword("ABCDEFGH12!X")).toEqual(["missing_mixed_case"]);
  });

  it("rejects forbidden words regardless of case", () => {
    expect(checkPassword("Xx9!PASSWORDzz")).toEqual(["contains_forbidden_word"]);
  });

  it("rejects three repeated characters but allows two", () => {
    expect(checkPassword("Aa1!bookkeeper")).toEqual([]);
    expect(checkPassword("Aa1!boookkeeper")).toEqual(["repeated_characters"]);
  });

  it("follows a custom policy", () => {
    const policy = { minLength: 4, requireDigit: false, requireSymbol: false, requireMixedCase: false, forbidden: ["cat"] };
    expect(checkPassword("dogs", policy)).toEqual([]);
    expect(checkPassword("Concatenate", policy)).toEqual(["contains_forbidden_word"]);
  });
});

describe("isValid", () => {
  it("is true only when there are no violations", () => {
    expect(isValid("Tr0ub4dor&Horse")).toBe(true);
    expect(isValid("short")).toBe(false);
  });
});

describe("strength", () => {
  it("rates a short lowercase password as weak", () => {
    expect(strength("abc")).toBe("weak");
  });

  it("scores length at 8 and 14 characters", () => {
    expect(strength("")).toBe("weak");
    expect(strength("abcdefg")).toBe("weak");
    expect(strength("abcdefgh")).toBe("weak");
    expect(strength("abcdefghijklm")).toBe("weak");
    expect(strength("abcdefghijklmn")).toBe("fair");
  });

  it("becomes fair at two points", () => {
    expect(strength("abcdefgh1")).toBe("fair");
    expect(strength("abcdefghijklm1")).toBe("fair");
  });

  it("becomes strong at four points", () => {
    expect(strength("abcdefghijkl1!")).toBe("strong");
    expect(strength("Abcdefg1!")).toBe("strong");
    expect(strength("Ab1!")).toBe("fair");
  });
});
