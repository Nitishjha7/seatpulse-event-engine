"""
Seat search route.

The process integrates three components:

    query (NL)  --ai.py-->  filters  --seat_search.py-->  matches
                   ^                        ^
                   |                        |
              optional                 always active

If the AI is disabled, fails, or cannot interpret the query, the system
falls back to standard filters to ensure search functionality remains
available.
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
    # ⭐ Rate limiting is critical due to AI costs.
    #
    # Each NL query triggers a paid API call. Rate limiting prevents
    # quota exhaustion from automated loops, which would otherwise
    # disable the feature for all users.
    dependencies=[Depends(limit_user(SEAT_LOCK))],
)
def search_seats(
    event_id: int,
    payload: SeatSearchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Search seats via natural language or explicit filters.

    Authentication is required to enforce per-user rate limits and
    attribute AI costs accurately, even though seat data is public.
    """
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    # Clear expired holds before searching to ensure only truly available
    # seats are returned.
    release_expired_locks(db, event_id)

    seats = db.scalars(
        select(Seat).where(Seat.event_id == event_id).order_by(Seat.row_label, Seat.seat_number)
    ).all()

    # Apply dynamic pricing to the current view. `seat.price` is the base
    # value (Phase 14).
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
    Determine filter source (user-provided vs. AI-interpreted).

    User-provided filters always take precedence. If a user manually
    adjusts filters after a query, the AI's interpretation is considered
    incorrect and overridden to prevent UX friction.
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
        # Fallback to default filters if AI fails or cannot parse the query.
        return SeatFilters(), False

    try:
        # ⭐ Validate model output. `understood` is internal metadata,
        # not a valid filter field.
        parsed.pop("understood", None)
        return SeatFilters(**parsed), True
    except Exception as exc:
        # Handle unexpected model output schema deviations.
        logger.warning("AI filter validation failed: %s", exc)
        return SeatFilters(), False
