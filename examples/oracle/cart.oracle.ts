import { describe, expect, it } from "vitest";
import { Cart, FREE_SHIPPING_THRESHOLD, MAX_QUANTITY, SHIPPING_FEE } from "../src/cart";

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

  it("exports its limits", () => {
    expect(MAX_QUANTITY).toBe(10);
    expect(FREE_SHIPPING_THRESHOLD).toBe(50);
    expect(SHIPPING_FEE).toBe(4.99);
  });

  it("defaults the quantity to one", () => {
    const cart = new Cart();
    cart.add("a", 3);
    expect(cart.itemCount).toBe(1);
  });

  it("accumulates quantity when the same sku is added twice", () => {
    const cart = new Cart();
    cart.add("a", 1, 2);
    cart.add("a", 1, 3);
    expect(cart.itemCount).toBe(5);
    expect(cart.subtotal()).toBe(5);
  });

  it("prices a line at the latest price", () => {
    const cart = new Cart();
    cart.add("a", 2);
    cart.add("a", 3);
    expect(cart.subtotal()).toBe(6);
  });

  it("rejects zero, negative and fractional quantities", () => {
    const cart = new Cart();
    for (const quantity of [0, -1, 1.5]) {
      expect(() => cart.add("a", 1, quantity)).toThrow("Quantity must be a positive integer");
    }
    expect(cart.itemCount).toBe(0);
  });

  it("allows free items but not negative prices", () => {
    const cart = new Cart();
    cart.add("gift", 0);
    expect(cart.subtotal()).toBe(0);
    expect(() => cart.add("a", -0.01)).toThrow("Price cannot be negative");
  });

  it("allows exactly the maximum quantity per line", () => {
    const cart = new Cart();
    cart.add("a", 1, 10);
    expect(cart.itemCount).toBe(10);
    expect(() => cart.add("a", 1, 1)).toThrow("Cannot have more than 10 of a");
    expect(cart.itemCount).toBe(10);
  });

  it("checks the maximum across repeated adds", () => {
    const cart = new Cart();
    cart.add("a", 1, 6);
    expect(() => cart.add("a", 1, 5)).toThrow("Cannot have more than 10 of a");
    cart.add("a", 1, 4);
    expect(cart.itemCount).toBe(10);
  });

  it("removes part of a line", () => {
    const cart = new Cart();
    cart.add("a", 2, 5);
    cart.remove("a", 2);
    expect(cart.itemCount).toBe(3);
    expect(cart.subtotal()).toBe(6);
  });

  it("removes the whole line when no quantity is given", () => {
    const cart = new Cart();
    cart.add("a", 2, 5);
    cart.remove("a");
    expect(cart.itemCount).toBe(0);
    expect(cart.shipping()).toBe(0);
  });

  it("removes the whole line when removing at least its quantity", () => {
    const cart = new Cart();
    cart.add("a", 1, 3);
    cart.remove("a", 3);
    expect(cart.shipping()).toBe(0);
    cart.add("b", 1, 2);
    cart.remove("b", 5);
    expect(cart.itemCount).toBe(0);
    expect(cart.total()).toBe(0);
  });

  it("ignores removing an unknown sku", () => {
    const cart = new Cart();
    cart.add("a", 1);
    cart.remove("b");
    expect(cart.itemCount).toBe(1);
  });

  it("rounds the subtotal to cents", () => {
    const cart = new Cart();
    cart.add("a", 0.1, 3);
    expect(cart.subtotal()).toBe(0.3);
  });

  it("has no discount without a coupon", () => {
    const cart = new Cart();
    cart.add("a", 10);
    expect(cart.discount()).toBe(0);
  });

  it("applies a percent coupon", () => {
    const cart = new Cart();
    cart.add("a", 80);
    cart.applyCoupon({ code: "TEN", kind: "percent", amount: 10 });
    expect(cart.discount()).toBe(8);
    expect(cart.total()).toBe(72);
  });

  it("rounds percent discounts to cents", () => {
    const cart = new Cart();
    cart.add("a", 3.33);
    cart.applyCoupon({ code: "TEN", kind: "percent", amount: 10 });
    expect(cart.discount()).toBe(0.33);
  });

  it("applies a fixed coupon", () => {
    const cart = new Cart();
    cart.add("a", 30);
    cart.applyCoupon({ code: "FIVE", kind: "fixed", amount: 5 });
    expect(cart.discount()).toBe(5);
    expect(cart.total()).toBe(29.99);
  });

  it("caps a fixed coupon at the subtotal", () => {
    const cart = new Cart();
    cart.add("a", 10);
    cart.applyCoupon({ code: "BIG", kind: "fixed", amount: 25 });
    expect(cart.discount()).toBe(10);
    expect(cart.total()).toBe(4.99);
  });

  it("enforces a coupon's minimum subtotal", () => {
    const cart = new Cart();
    cart.add("a", 20);
    expect(() => cart.applyCoupon({ code: "BIG", kind: "fixed", amount: 5, minSubtotal: 25 })).toThrow(
      "Coupon BIG requires a subtotal of at least 25",
    );
    expect(cart.discount()).toBe(0);
  });

  it("accepts a coupon exactly at its minimum subtotal", () => {
    const cart = new Cart();
    cart.add("a", 25);
    cart.applyCoupon({ code: "BIG", kind: "fixed", amount: 5, minSubtotal: 25 });
    expect(cart.discount()).toBe(5);
  });

  it("charges nothing for an empty cart", () => {
    const cart = new Cart();
    expect(cart.shipping()).toBe(0);
    expect(cart.total()).toBe(0);
  });

  it("ships for free at exactly the threshold", () => {
    const cart = new Cart();
    cart.add("a", 50);
    expect(cart.shipping()).toBe(0);
    expect(cart.total()).toBe(50);
  });

  it("charges shipping just below the threshold", () => {
    const cart = new Cart();
    cart.add("a", 49.99);
    expect(cart.shipping()).toBe(4.99);
    expect(cart.total()).toBe(54.98);
  });

  it("decides free shipping after the discount", () => {
    const cart = new Cart();
    cart.add("a", 60);
    cart.applyCoupon({ code: "TWENTY", kind: "fixed", amount: 20 });
    expect(cart.shipping()).toBe(4.99);
    expect(cart.total()).toBe(44.99);
  });
});
