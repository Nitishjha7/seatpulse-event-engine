"""
Seat search ka route.

Yahan teen cheezein judti hain:

    query (NL)  --ai.py-->  filters  --seat_search.py-->  matches
                   ^                        ^
                   |                        |
              optional                 hamesha chalta hai

AI band ho, fail ho jaye, ya query samajh na aaye — filters phir bhi
lagte hain aur search phir bhi chalta hai. Sirf natural language wala
input band hota hai.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

import ai
import seat_search
from auth import get_current_user
from database import get_db
from models import Event, Seat, User
from pricing import current_price
from pricing_state import pricing_state
from rate_limit import SEAT_LOCK, limit_user
from routers.seats import release_expired_locks
from schemas import SeatFilters, SeatMatch, SeatSearchOut, SeatSearchRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["search"])


@router.post(
    "/{event_id}/seats/search",
    response_model=SeatSearchOut,
    # ⭐ Rate limited, aur ye AI wali wajah se aur zaroori hai.
    #
    # Har NL query ek paid API call hai. Bina limit ke koi bhi loop
    # chala ke quota khatam kar sakta hai — aur uska matlab sirf paisa
    # nahi, feature sabke liye band ho jana hai.
    dependencies=[Depends(limit_user(SEAT_LOCK))],
)
def search_seats(
    event_id: int,
    payload: SeatSearchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Seats dhoondo — natural language se ya seedhe filters se.

    Login zaroori hai. Ye sirf isliye nahi ki data private hai (seats
    public hain), balki isliye ki rate limit per-user lagti hai aur AI
    calls ka kharcha kisi ke naam hona chahiye.
    """
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event nahi mila")

    # Search se pehle expired holds saaf — warna jo seats asal me free ho
    # chuki hain wo results me aati hi nahi
    release_expired_locks(db, event_id)

    seats = db.scalars(
        select(Seat).where(Seat.event_id == event_id).order_by(Seat.row_label, Seat.seat_number)
    ).all()

    # Dynamic pricing on ho to filter CURRENT price par lagna chahiye —
    # wahi user ko dikh raha hai. `seat.price` base hai (Phase 14).
    info = pricing_state(db, event)
    for seat in seats:
        seat._display_price = current_price(float(seat.price), info)

    filters, interpreted = _resolve_filters(payload, event, seats, event_id)

    matches = seat_search.find(
        seats,
        quantity=filters.quantity,
        together=filters.together,
        min_price=filters.min_price,
        max_price=filters.max_price,
        section=filters.section,
        row_preference=filters.row_preference,
        layout=event.layout,
    )

    return SeatSearchOut(
        matches=[
            SeatMatch(
                seat_ids=m.seat_ids,
                label=m.label,
                row_label=m.row_label,
                section=m.section,
                seat_numbers=m.seat_numbers,
                total_price=m.total_price,
            )
            for m in matches
        ],
        filters=filters,
        interpreted=interpreted,
    )


def _resolve_filters(
    payload: SeatSearchRequest, event: Event, seats: list, event_id: int
) -> tuple[SeatFilters, bool]:
    """
    Kaunse filters lagenge — aur wo AI se aaye ya user se.

    Priority: user ke apne filters HAMESHA jeetenge.

    Wajah: agar user ne query likhne ke baad dropdown se kuch badla hai,
    to uska matlab hai ki AI ki samajh galat thi. Us par AI ka jawab
    thopna user ko ladne pe majboor karta hai apne hi search box se.
    """
    if payload.filters is not None:
        return payload.filters, False

    if not payload.query or not ai.is_enabled():
        return SeatFilters(), False

    sections = sorted({s.section for s in seats if s.section})
    prices = [s._display_price for s in seats] or [0]

    parsed = ai.parse_query(
        payload.query,
        event_id=event_id,
        sections=sections,
        price_range=(min(prices), max(prices)),
    )
    if parsed is None:
        # Samajh nahi aaya ya call fail hui — khali filters ke saath sab
        # available seats dikha do. Ye "kuch nahi mila" se behtar hai.
        return SeatFilters(), False

    try:
        # ⭐ Yahi wo jagah hai jahan model ka output validate hota hai.
        # `understood` hamara apna field hai, filters ka nahi.
        parsed.pop("understood", None)
        return SeatFilters(**parsed), True
    except Exception as exc:
        # Model ne schema ke bawajood kuch ajeeb bhej diya. Ye hona nahi
        # chahiye, par "nahi hona chahiye" aur "nahi hoga" alag baatein hain.
        logger.warning("AI filters validate nahi hue: %s", exc)
        return SeatFilters(), False
