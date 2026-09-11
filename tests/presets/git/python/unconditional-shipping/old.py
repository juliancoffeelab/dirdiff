"""Calculate checkout charges from membership status and a delivery quote.

The caller supplies nonnegative Decimal amounts. No account or cart state is kept.
"""

from decimal import Decimal

__all__ = ["checkout_total"]


def checkout_total(subtotal: Decimal, shipping: Decimal, member: bool) -> Decimal:
    """Calculate the payable amount using the supplied membership status."""
    total = subtotal
    if member:
        total *= Decimal("0.90")
        total += shipping
    return total.quantize(Decimal("0.01"))
