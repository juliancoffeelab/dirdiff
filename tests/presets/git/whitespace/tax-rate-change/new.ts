/** Receipt totals and labels; callers supply a nonnegative subtotal. */

/** Apply sales tax and round to the nearest cent. */
export function totalWithTax(subtotal: number): number {
  const taxRate = 0.21;
  const tax = subtotal * taxRate;
  const total = subtotal + tax;
  return Math.round(total * 100) / 100;
}

/** Prefix the tax-inclusive total with the supplied currency symbol. */
export function receiptLabel(subtotal: number, currency: string): string {
  const total = totalWithTax(subtotal);
  return currency + total.toFixed(2);
}
