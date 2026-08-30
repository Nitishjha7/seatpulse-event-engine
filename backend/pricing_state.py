"""
Event ki pricing state nikalne ka shared helper.

Alag file isliye ki seats, events, bookings aur payments — sab ise use
karte hain, aur kisi ek router me rakhne se circular imports ban jaate.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import BOOKING_CONFIRMED, Booking, Event, Seat
from pricing import PricingInfo, current_price, pricing_for_event


def pricing_state(db: Session, event: Event) -> PricingInfo:
    """
    Event ki abhi ki pricing.

    `sold` ke liye CONFIRMED bookings ginte hain, `seats.status='booked'`
    nahi — dono almost same hote hain, par bookings hi paise ka sach hai.
    """
    total = db.scalar(select(func.count(Seat.id)).where(Seat.event_id == event.id)) or 0
    sold = db.scalar(
        select(func.count(Booking.id)).where(
            Booking.event_id == event.id, Booking.status == BOOKING_CONFIRMED
        )
    ) or 0

    # sample_base — "kitni seats me price badhega" ka andaza is price par
    # lagta hai. Sabse sasti seat use karte hain, kyunki wahi sabse pehle
    # bikti hai aur user usi ko dekh raha hota hai.
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
    Is seat ka abhi ka price.

    ⚠️ Hold me hai to LOCKED price milta hai — wahi jo user ko dikhaya tha.
    Ye is poore feature ka sabse zaroori niyam hai.
    """
    if seat.held_price is not None:
        return float(seat.held_price)

    event = db.get(Event, seat.event_id)
    return current_price(float(seat.price), pricing_state(db, event))
