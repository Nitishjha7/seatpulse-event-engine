"""
Shared helper to determine the pricing state of an event. Split out to
avoid circular imports — seats, events, bookings, and payments all use it.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import BOOKING_CONFIRMED, Booking, Event, Seat
from services.pricing import PricingInfo, current_price, pricing_for_event


def pricing_state(db: Session, event: Event) -> PricingInfo:
    """
    Calculates the current pricing state for an event. Uses CONFIRMED
    bookings for the `sold` count rather than seat status, since bookings
    are the source of truth for revenue.
    """
    total = db.scalar(select(func.count(Seat.id)).where(Seat.event_id == event.id)) or 0
    sold = db.scalar(
        select(func.count(Booking.id)).where(
            Booking.event_id == event.id, Booking.status == BOOKING_CONFIRMED
        )
    ) or 0

    # sample_base uses the cheapest seat price to estimate surge thresholds,
    # since the lowest-priced seats are usually first to sell.
    sample = db.scalar(select(func.min(Seat.price)).where(Seat.event_id == event.id))

    return pricing_for_event(
        enabled=bool(event.dynamic_pricing),
        sold=sold,
        total=total,
        demand_factor=float(event.demand_factor),
        max_surge=float(event.max_surge),
        sample_base=float(sample) if sample else 1000.0,
    )


def price_now(db: Session, seat: Seat) -> float:
    """
    Returns the current price for a specific seat. If it's held, returns the
    locked price originally shown to the user, so the price can't drift
    mid-checkout.
    """
    if seat.held_price is not None:
        return float(seat.held_price)

    event = db.get(Event, seat.event_id)
    return current_price(float(seat.price), pricing_state(db, event))
