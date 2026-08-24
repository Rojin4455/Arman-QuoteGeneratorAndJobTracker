from decimal import Decimal, ROUND_HALF_UP

CENTS_QUANT = Decimal("0.01")


def dollars_to_cents(value) -> int:
    """Convert a dollar amount to a non-negative integer number of cents."""
    if value is None:
        return 0
    amount = Decimal(str(value)).quantize(CENTS_QUANT, rounding=ROUND_HALF_UP)
    cents = int(amount * 100)
    if cents < 0:
        raise ValueError("Money amounts cannot be negative.")
    return cents


def cents_to_dollars(cents: int) -> Decimal:
    """Convert integer cents to a 2-decimal dollar amount."""
    if cents is None:
        return Decimal("0.00")
    if not isinstance(cents, int):
        raise ValueError("Cents must be an integer.")
    return (Decimal(cents) / Decimal(100)).quantize(CENTS_QUANT)


def assert_cents(value, label="Amount"):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer number of cents.")
    return value
