"""Build checkout details and a printable receipt from a priced cart."""

__all__ = ["checkout"]


def checkout(cart: dict, print_receipt: bool) -> dict:
    """Return checkout details; the receipt uses the cart's quoted amounts."""
    receipt = []

    subtotal = cart["subtotal"]
    shipping = cart["shipping"]
    discount = cart["discount"]
    taxable = max(subtotal - discount, 0)
    tax = round(taxable * cart["tax_rate"], 2)
    total = round(taxable + tax + shipping, 2)
    summary = {
        "number": cart["number"],
        "subtotal": subtotal,
        "shipping": shipping,
        "discount": discount,
        "tax": tax,
        "total": total,
    }

    receipt.append(f"Order: {cart['number']}")
    receipt.append(f"Customer: {cart['customer']}")
    receipt.append(f"Subtotal: {cart['subtotal']:.2f}")
    receipt.append(f"Shipping: {cart['shipping']:.2f}")
    receipt.append("Thank you for shopping with us.")

    return {"summary": summary, "receipt": receipt}
