"""
Dynamic pricing based on demand.

Airlines, Uber, and concert tickets use this model: prices increase as
inventory depletes.

---- Design Decision ----

The `price` column in the seats table is immutable; it represents the BASE price.

The current price is calculated as: base × multiplier

Rationale for immutability:
  - Preserves historical booking references (original price paid).
  - Avoids updating thousands of rows per booking.
  - Maintains a clear audit trail of the base price.
  - Prevents race conditions during concurrent booking updates.

By keeping the base immutable and calculating the multiplier dynamically,
we ensure data integrity and high performance.

---- Formula ----

    sold_ratio = booked_seats / total_seats
    multiplier = 1 + (sold_ratio × demand_factor)
    multiplier = min(multiplier, max_surge)

    current_price = round(base × multiplier)

A demand_factor of 0.5 means the price increases by 1.5× when 100% sold.
The increase is linear.

This implementation is intentionally simple. Real-world surge pricing
incorporates time-to-event, booking velocity, and historical demand, but
those require significant data to avoid guesswork. This formula is
transparent, allowing us to explain price changes clearly to users.
"""

from dataclasses import dataclass

# Rounding increment.
# Prices are rounded to ₹10 to avoid awkward figures like ₹827.43,
# which can erode user trust.
ROUND_TO = 10


@dataclass(frozen=True)
class PricingInfo:
    """Current pricing state for an event."""

    enabled: bool
    multiplier: float
    sold_ratio: float
    sold: int
    total: int
    # Seats remaining before the next price increase.
    # None if pricing is disabled or max surge is reached.
    seats_until_increase: int | None

    @property
    def surge_percent(self) -> int:
        """Percentage increase over base price for UI display."""
        return round((self.multiplier - 1) * 100)


def multiplier_for(sold: int, total: int, demand_factor: float, max_surge: float) -> float:
    """
    Calculates the multiplier based on demand.

    Isolated to facilitate testing and reuse in seat-threshold calculations.
    """
    if total <= 0:
        return 1.0

    sold_ratio = min(1.0, sold / total)
    return min(max_surge, 1.0 + sold_ratio * demand_factor)


def apply(base_price: float, multiplier: float) -> float:
    """
    Applies the multiplier to the base price and rounds the result.

    Python's round() uses banker's rounding (e.g., 100.5 -> 100, 101.5 -> 102).
    This balances out over time.

    Note: Due to rounding, the final price may remain constant even if the
    multiplier increases slightly. Therefore, _seats_until_increase
    simulates price changes rather than estimating them.
    """
    raw = base_price * multiplier
    return float(round(raw / ROUND_TO) * ROUND_TO)


def current_price(base_price: float, info: PricingInfo) -> float:
    if not info.enabled:
        return float(base_price)
    return apply(base_price, info.multiplier)


def _seats_until_increase(
    sold: int, total: int, demand_factor: float, max_surge: float, sample_base: float
) -> int | None:
    """
    Calculates how many more seats must be sold before the price increases.

    ⚠️ This is an estimate based on a sample base price. Different price
    tiers will trigger increases at different points. Used in the UI to
    display "N seats left at this price."

    The loop is bounded by remaining inventory and is computationally
    inexpensive. Calculated on-demand to ensure accuracy, as cached
    pricing data could be misleading.
    """
    if total <= 0 or sold >= total:
        return None

    now = apply(sample_base, multiplier_for(sold, total, demand_factor, max_surge))

    for extra in range(1, total - sold + 1):
        later = apply(sample_base, multiplier_for(sold + extra, total, demand_factor, max_surge))
        if later > now:
            return extra

    # Reached max surge or price is stable due to rounding.
    return None


def pricing_for_event(
    *,
    enabled: bool,
    sold: int,
    total: int,
    demand_factor: float,
    max_surge: float,
    sample_base: float = 1000.0,
) -> PricingInfo:
    """Aggregates the complete pricing state for an event."""
    if not enabled:
        return PricingInfo(
            enabled=False,
            multiplier=1.0,
            sold_ratio=0.0 if total <= 0 else sold / total,
            sold=sold,
            total=total,
            seats_until_increase=None,
        )

    return PricingInfo(
        enabled=True,
        multiplier=multiplier_for(sold, total, demand_factor, max_surge),
        sold_ratio=0.0 if total <= 0 else min(1.0, sold / total),
        sold=sold,
        total=total,
        seats_until_increase=_seats_until_increase(
            sold, total, demand_factor, max_surge, sample_base
        ),
    )
