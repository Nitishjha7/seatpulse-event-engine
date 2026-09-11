"""
Helper to notify all clients of seat changes.

Separated into this file to decouple routers from WebSocket implementation details;
routers only need to call `broadcast_seat_update(db, seat_id, "locked")`.
"""

from sqlalchemy.orm import Session

from models import Event, Seat
from pricing import current_price
from pricing_state import pricing_state
from schemas import PricingOut, SeatOut
from websocket import publish

# These actions alter the sold seat count, triggering a global event demand
# multiplier update.
#
# Centralized here to ensure pricing broadcasts are not omitted when adding
# new routes, preventing stale prices on the frontend.
_SOLD_COUNT_CHANGED = ("booked", "cancelled")


def broadcast_seat_update(db: Session, seat_id: int, action: str) -> None:
    """
    Broadcast the new seat state to all connected clients.

    The `action` parameter (locked / released / booked / cancelled) is used
    for frontend logic, debugging, and logging.
    """
    seat = db.get(Seat, seat_id)
    if seat is None:
        return

    # IMPORTANT: Routers use `update()` with synchronize_session=False,
    # leaving the session cache stale. Refresh required to broadcast current state.
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
    """Broadcast updated pricing to all clients when knobs are adjusted."""
    event = db.get(Event, event_id)
    if event is None:
        return
    _publish_pricing(event_id, pricing_state(db, event))


def _publish_pricing(event_id: int, info) -> None:
    """
    Broadcasts EVENT-level pricing only, rather than individual seat prices.

    Avoids payload bloat for large events (e.g., 500 seats). Since the multiplier
    is global and the base price is cached on the frontend, the client can
    calculate the current price locally.
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
