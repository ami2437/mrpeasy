"""Money rounding, one rule everywhere: each line's amount is rounded to the cent (half up),
and a document's total is the sum of its rounded lines -- so what's printed always adds up.
(MRPeasy's imported invoices all have whole-cent lines, so this matches them exactly.)"""
from decimal import Decimal, ROUND_HALF_UP


def cents(value) -> float:
    """Round to the cent, half up (2.675 -> 2.68, not banker's or float-artefact 2.67)."""
    return float(Decimal(repr(float(value or 0))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def line_amount(quantity, unit_price) -> float:
    """quantity x price, rounded to the cent. Prices carry up to 5 decimals ($2.55588)."""
    return cents(Decimal(repr(float(quantity or 0))) * Decimal(repr(float(unit_price or 0))))


def total(lines, price_attr: str = "unit_price") -> float:
    return cents(sum(line_amount(l.quantity, getattr(l, price_attr)) for l in lines))
