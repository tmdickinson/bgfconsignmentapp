"""Money is always integer cents."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def format_cents(cents):
    if cents is None:
        return ""
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def parse_dollars(text):
    """Turn "2", "2.5" or "$2.50" into cents. Raises ValueError on anything else."""
    cleaned = (text or "").strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as e:
        raise ValueError(f"Not an amount: {text!r}") from e
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise ValueError("Amounts can't have fractions of a cent.")
    return int(cents)


def percent_of(cents, percent):
    """A whole-number percentage of an amount, rounded half up."""
    return int((Decimal(cents) * percent / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
