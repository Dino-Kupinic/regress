export interface CartLine {
  sku: string;
  price: number;
  quantity: number;
}

export interface Coupon {
  code: string;
  kind: "percent" | "fixed";
  amount: number;
  minSubtotal?: number;
}

export const MAX_QUANTITY = 10;
export const FREE_SHIPPING_THRESHOLD = 50;
export const SHIPPING_FEE = 4.99;

export class Cart {
  private lines = new Map<string, CartLine>();
  private coupon: Coupon | null = null;

  add(sku: string, price: number, quantity = 1): void {
    if (!Number.isInteger(quantity) || quantity <= 0) {
      throw new Error("Quantity must be a positive integer");
    }
    if (price < 0) {
      throw new Error("Price cannot be negative");
    }
    const existing = this.lines.get(sku);
    const newQuantity = (existing?.quantity ?? 0) + quantity;
    if (newQuantity > MAX_QUANTITY) {
      throw new Error(`Cannot have more than ${MAX_QUANTITY} of ${sku}`);
    }
    this.lines.set(sku, { sku, price, quantity: newQuantity });
  }

  remove(sku: string, quantity?: number): void {
    const existing = this.lines.get(sku);
    if (!existing) {
      return;
    }
    if (quantity === undefined || quantity >= existing.quantity) {
      this.lines.delete(sku);
      return;
    }
    existing.quantity -= quantity;
  }

  get itemCount(): number {
    let count = 0;
    for (const line of this.lines.values()) {
      count += line.quantity;
    }
    return count;
  }

  subtotal(): number {
    let sum = 0;
    for (const line of this.lines.values()) {
      sum += line.price * line.quantity;
    }
    return round2(sum);
  }

  applyCoupon(coupon: Coupon): void {
    if (coupon.minSubtotal !== undefined && this.subtotal() < coupon.minSubtotal) {
      throw new Error(`Coupon ${coupon.code} requires a subtotal of at least ${coupon.minSubtotal}`);
    }
    this.coupon = coupon;
  }

  discount(): number {
    if (!this.coupon) {
      return 0;
    }
    const subtotal = this.subtotal();
    const raw = this.coupon.kind === "percent" ? subtotal * (this.coupon.amount / 100) : this.coupon.amount;
    return round2(Math.min(raw, subtotal));
  }

  shipping(): number {
    if (this.lines.size === 0) {
      return 0;
    }
    return this.subtotal() - this.discount() >= FREE_SHIPPING_THRESHOLD ? 0 : SHIPPING_FEE;
  }

  total(): number {
    return round2(this.subtotal() - this.discount() + this.shipping());
  }
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}
