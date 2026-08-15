"""
Seat change hone par sab clients ko batane ka helper.

Alag file isliye taki routers ko WebSocket ki detail na pata ho — unhe bas
`broadcast_seat_update(db, seat_id, "locked")` call karna hai.
"""

from sqlalchemy.orm import Session

from models import Seat
from schemas import SeatOut
from websocket import publish


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

    publish(
        seat.event_id,
        {
            "type": "seat_update",
            "action": action,
            "seat": SeatOut.model_validate(seat).model_dump(mode="json"),
        },
    )
