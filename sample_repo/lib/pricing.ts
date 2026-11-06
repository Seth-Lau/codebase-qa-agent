// All money is in whole pence, so there are no floating-point rounding errors.

export const VAT_RATE = 0.2; // UK standard rate, 20%
export const BULK_DISCOUNT_RATE = 0.05; // 5% off
export const BULK_DISCOUNT_MIN_QTY = 10; // applies from 10 items upwards

/** Total for one order line, including any bulk discount and VAT, rounded to the nearest penny. */
export function calculateTotal(unitPricePence: number, quantity: number): number {
  let subtotal = unitPricePence * quantity;
  if (quantity >= BULK_DISCOUNT_MIN_QTY) {
    subtotal = subtotal * (1 - BULK_DISCOUNT_RATE);
  }
  return Math.round(subtotal * (1 + VAT_RATE));
}
