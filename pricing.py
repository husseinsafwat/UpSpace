"""Pure pricing rules. No UI and no database, so they are easy to test.

ROOM  : exact minutes / 60 x discounted hourly price. No rounding, no minimum.
        Discount is per card: EGP off each hour, or % off the hourly price.
HALL  : billed hours = at least 1; after that a leftover under 30 min is dropped,
        30 min or more counts as a full hour.
        Per chair: tier prices for hours 1-4 (default 15, 15, 10, 10, so max 50),
        x number of chairs. The tiers are edited in Settings and copied onto each
        hall card when it starts.
        No discount for the hall.
ITEMS : drinks / add-ons are added at full price, never discounted.
"""
from __future__ import annotations

from config import HALL_HOUR_TIERS

DISCOUNT_NONE = "none"
DISCOUNT_EGP = "egp"   # EGP off the hourly price
DISCOUNT_PCT = "pct"   # percentage off the hourly price


def elapsed_minutes(seconds: float) -> int:
    """Whole minutes that have fully passed."""
    return max(0, int(seconds // 60))


def discounted_rate(price_per_hour: float, discount_type: str, discount_value: float) -> float:
    price = float(price_per_hour)
    value = float(discount_value or 0)
    if discount_type == DISCOUNT_EGP:
        rate = price - value
    elif discount_type == DISCOUNT_PCT:
        rate = price * (1 - value / 100.0)
    else:
        rate = price
    return max(0.0, rate)


def room_time_cost(minutes: int, price_per_hour: float,
                   discount_type: str = DISCOUNT_NONE, discount_value: float = 0) -> float:
    rate = discounted_rate(price_per_hour, discount_type, discount_value)
    return round(minutes / 60.0 * rate, 2)


def hall_billed_hours(minutes: int) -> int:
    if minutes < 60:
        return 1
    hours, rest = divmod(minutes, 60)
    return hours + (1 if rest >= 30 else 0)


def hall_price_per_chair(billed_hours: int, tiers=None) -> float:
    """Sum of the tier prices for the billed hours. Hours past the last tier are free,
    so a chair never costs more than sum(tiers)."""
    tiers = HALL_HOUR_TIERS if tiers is None else tiers
    return float(sum(tiers[:max(0, billed_hours)]))


def hall_time_cost(minutes: int, chairs: int, tiers=None) -> float:
    return round(hall_price_per_chair(hall_billed_hours(minutes), tiers) * int(chairs), 2)


def tiers_to_text(tiers) -> str:
    return ",".join(fmt_money(t).replace(",", "") for t in tiers)


def tiers_from_text(text: str | None):
    if not text:
        return None
    return [float(x) for x in text.split(",") if x.strip()]


def validate_discount(discount_type: str, discount_value: float, price_per_hour: float) -> str | None:
    """Return an error message, or None if the discount is fine."""
    if discount_type == DISCOUNT_NONE:
        return None
    if discount_value < 0:
        return "Discount cannot be negative."
    if discount_type == DISCOUNT_PCT and discount_value > 100:
        return "Percentage discount cannot be more than 100%."
    if discount_type == DISCOUNT_EGP and discount_value > price_per_hour:
        return "Discount cannot be more than the room's hourly price."
    return None


def fmt_money(value: float) -> str:
    value = round(float(value), 2)
    return f"{value:,.0f}" if value == int(value) else f"{value:,.2f}"


def fmt_duration(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m:02d}m"
