"""Calculate checkout totals from item prices and a quoted shipping fee.

The caller supplies nonnegative Decimal amounts. This module keeps no cart state.
"""

from decimal import Decimal

__all__ = ["checkout_total"]


def checkout_total(prices: list[Decimal], shipping: Decimal) -> Decimal:
    """Return the checkout amount with free shipping for orders of at least 100."""
    total = sum(prices, Decimal("0"))
    if total < Decimal("100"):
        total += shipping
    return total.quantize(Decimal("0.01"))
