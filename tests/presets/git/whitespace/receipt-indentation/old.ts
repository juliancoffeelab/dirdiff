/** Plain-text receipt rendering; this module stores no invoice state. */

/** Format line items and their sum, using the caller's currency symbol. */
export function formatReceipt(amounts: readonly number[], currency: string): string {
    const lines = amounts.map((amount) => {
        return `${currency}${amount.toFixed(2)}`;
    });
    const total = amounts.reduce((sum, amount) => sum + amount, 0);
    lines.push(`Total: ${currency}${total.toFixed(2)}`);
    return lines.join("\n");
}
