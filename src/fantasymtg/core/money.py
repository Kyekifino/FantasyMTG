"""Money is stored as integer cents everywhere to avoid floating point drift."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

Cents = int


def parse_money(raw: str) -> Cents:
    """Parse "$12.34", "12.34" or "12" into cents. Raises ValueError on bad input."""
    text = raw.strip().replace(",", "")
    if text.startswith("$"):
        text = text[1:].strip()
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"'{raw}' is not a dollar amount") from exc
    if not value.is_finite():
        raise ValueError(f"'{raw}' is not a dollar amount")
    cents = value * 100
    if cents != cents.to_integral_value():
        raise ValueError(f"'{raw}' has more than two decimal places")
    return int(cents)


def dollars_to_cents(value: str | float | int | None) -> Cents | None:
    """Convert a price as reported by a price source (e.g. "1.23") to cents."""
    if value is None:
        return None
    try:
        dec = Decimal(str(value))
    except InvalidOperation:
        return None
    return int((dec * 100).quantize(Decimal(1)))


def format_money(cents: Cents) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def format_change(cents: Cents) -> str:
    """Like format_money, but always signed: "+$1.50", "-$0.25", "$0.00"."""
    return ("+" if cents > 0 else "") + format_money(cents)
