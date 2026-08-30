"""
Seat change hone par sab clients ko batane ka helper.

Alag file isliye taki routers ko WebSocket ki detail na pata ho — unhe bas
`broadcast_seat_update(db, seat_id, "locked")` call karna hai.
"""

from sqlalchemy.orm import Session

from models import Event, Seat
from pricing import current_price
from pricing_state import pricing_state
from schemas import PricingOut, SeatOut
from websocket import publish

# Ye actions bikee hui seats ki ginti badalte hain -- matlab poore event ka
# demand multiplier badal jata hai, sirf ek seat ka nahi.
#
# List yahan rakhi hai (call site pe nahi) taki koi naya route add karte
# waqt pricing broadcast bhoolna MUMKIN hi na ho. Bhool jaate to grid me
# stale prices dikhte rehte jab tak user refresh na kare.
_SOLD_COUNT_CHANGED = ("booked", "cancelled")


def broadcast_seat_update(db: Session, seat_id: int, action: str) -> None:
    """
    Ek seat ka naya state sab connected clients ko bhejo.

    `action` sirf batane ke liye hai (locked / released / booked / cancelled) —
    frontend seat object se hi sab kuch samajh leta hai. Debugging aur logs
    me kaam aata hai.
    """
    seat = db.get(Seat, seat_id)
    if seat is None:
        return

    # ZAROORI: routers me `update()` statement se seat badli hai, aur wo
    # session ke cached object ko update nahi karta (synchronize_session=False).
    # Bina refresh ke purana status broadcast ho jayega.
    db.refresh(seat)

    event = db.get(Event, seat.event_id)
    info = pricing_state(db, event)

    publish(
        seat.event_id,
        {
            "type": "seat_update",
            "action": action,
            "seat": {
                **SeatOut.model_validate(seat).model_dump(mode="json"),
                "current_price": current_price(float(seat.price), info),
            },
        },
    )

    if action in _SOLD_COUNT_CHANGED:
        _publish_pricing(seat.event_id, info)


def broadcast_pricing_update(db: Session, event_id: int) -> None:
    """Organizer ne pricing knobs badle -- sabko turant naya price dikhao."""
    event = db.get(Event, event_id)
    if event is None:
        return
    _publish_pricing(event_id, pricing_state(db, event))


def _publish_pricing(event_id: int, info) -> None:
    """
    Sirf EVENT-level pricing bhejte hain, har seat ka naya price nahi.

    500 seats wale event me har booking par 500 seat objects bhejna paagalpan
    hoga. Multiplier poore event ka ek hi hai, aur base price frontend ke paas
    pehle se hai -- wo khud `base x multiplier` kar leta hai.

    Ek chhota message vs 500 -- aur dono ka result bilkul same.
    """
    publish(
        event_id,
        {
            "type": "pricing_update",
            "pricing": PricingOut(
                enabled=info.enabled,
                multiplier=round(info.multiplier, 3),
                surge_percent=info.surge_percent,
                sold=info.sold,
                total=info.total,
                seats_until_increase=info.seats_until_increase,
            ).model_dump(mode="json"),
        },
    )
