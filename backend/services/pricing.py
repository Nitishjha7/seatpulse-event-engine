"""
Dynamic pricing based on demand — the same model airlines, Uber, and
concert tickets use: prices rise as inventory depletes.

The `price` column on seats is immutable — it's the BASE price, and the
current price is just base × multiplier. Keeping it immutable preserves
what each booking actually paid, avoids rewriting thousands of rows per
sale, keeps a clean audit trail, and sidesteps races from concurrent
booking updates.

Formula:

    sold_ratio = booked_seats / total_seats
    multiplier = min(1 + sold_ratio * demand_factor, max_surge)
    current_price = round(base * multiplier)

demand_factor 0.5 means price is 1.5x at 100% sold, and the increase is
linear. This is intentionally simple — real surge pricing would factor in
time-to-event, booking velocity, and historical demand, but that needs a
lot of data to avoid guessing, and this formula stays easy to explain to
users.
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
    """Calculates the multiplier based on demand. Split out so it's reusable in seat-threshold calculations."""
    if total <= 0:
        return 1.0

    sold_ratio = min(1.0, sold / total)
    return min(max_surge, 1.0 + sold_ratio * demand_factor)


def apply(base_price: float, multiplier: float) -> float:
    """
    Applies the multiplier to the base price and rounds. Python's round()
    uses banker's rounding (100.5 -> 100, 101.5 -> 102), which balances out
    over time. Because of rounding, the displayed price can stay flat even
    as the multiplier creeps up — that's why `_seats_until_increase`
    simulates price changes instead of estimating them analytically.
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
    Estimates how many more seats must sell before the price ticks up, based
    on a sample base price (different tiers will trigger at different
    points). Powers the "N seats left at this price" UI text.

    The loop is bounded by remaining inventory, so it's cheap, and it's run
    on-demand rather than cached since stale pricing data could mislead users.
    """
    if total <= 0 or sold >= total:
        return None

    now = apply(sample_base, multiplier_for(sold, total, demand_factor, max_surge))

    for extra in range(1, total - sold + 1):
        later = apply(sample_base, multiplier_for(sold + extra, total, demand_factor, max_surge))
        if later > now:
            return extra

    # Either max surge is reached, or rounding keeps the price stable.
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
