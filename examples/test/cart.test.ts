import { describe, expect, it } from "vitest";
import { Cart } from "../src/cart";

describe("Cart", () => {
  it("sums line prices into the subtotal", () => {
    const cart = new Cart();
    cart.add("apple", 1.5, 2);
    cart.add("pear", 2);
    expect(cart.subtotal()).toBe(5);
  });

  it("charges shipping on small orders", () => {
    const cart = new Cart();
    cart.add("apple", 10);
    expect(cart.total()).toBeGreaterThan(10);
  });

  it("rejects negative prices", () => {
    expect(() => new Cart().add("apple", -1)).toThrow();
  });
});
