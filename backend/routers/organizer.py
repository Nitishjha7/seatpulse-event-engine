"""
Organizer routes — create and manage events.

RBAC is implemented here: these endpoints are restricted to users with
`organizer` or `admin` roles.

Distinguish between these concepts:

  AUTHENTICATION  — Who are you?          (Phase 7, via token)
  AUTHORIZATION   — What can you do?      (This phase, via role)

And a third, equally critical concept:

  OWNERSHIP       — Is this resource yours?

Role checks are insufficient. An `organizer` role does not grant
permission to edit any event — only those they own.
"""

import string

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from auth import require_role
import ai
from database import get_db
from rate_limit import BOOKING, limit_user
# Alias `layout` to avoid shadowing local variables.
import layout as seat_layout
from events_broadcast import broadcast_pricing_update
from models import (
    BOOKING_CONFIRMED,
    ROLE_ADMIN,
    ROLE_ORGANIZER,
    SEAT_AVAILABLE,
    SEAT_BOOKED,
    SEAT_LOCKED,
    Booking,
    Event,
    Seat,
    User,
)
from schemas import EventDraftOut, EventDraftRequest, EventCreate, EventUpdate, OrganizerEventOut

router = APIRouter(prefix="/api/organizer", tags=["organizer"])

# Maximum seats per event. Prevents database bloat from excessive row/seat creation.
MAX_SEATS_PER_EVENT = 2000

ROW_LABELS = string.ascii_uppercase   # A..Z


def _owned_event(event_id: int, user: User, db: Session) -> Event:
    """
    Retrieve an event only if owned by the user (or if user is admin).

    ⚠️ This check is required for all organizer endpoints to prevent IDOR,
    where an organizer could otherwise modify another's event.
    """
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    if user.role != ROLE_ADMIN and event.organizer_id != user.id:
        # Return 404 instead of 403 to prevent leaking event existence.
        # Consistent with IDOR fixes in booking endpoints.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    return event


def _event_stats(db: Session, event_ids: list[int]) -> dict[int, dict]:
    """
    Fetch aggregate counts for multiple events.

    ⚠️ Use a single query to avoid N+1 performance issues.
    """
    if not event_ids:
        return {}

    seat_rows = db.execute(
        select(Seat.event_id, Seat.status, func.count(Seat.id))
        .where(Seat.event_id.in_(event_ids))
        .group_by(Seat.event_id, Seat.status)
    ).all()

    revenue_rows = db.execute(
        select(Booking.event_id, func.coalesce(func.sum(Booking.amount), 0))
        .where(Booking.event_id.in_(event_ids), Booking.status == BOOKING_CONFIRMED)
        .group_by(Booking.event_id)
    ).all()

    stats = {
        eid: {SEAT_AVAILABLE: 0, SEAT_LOCKED: 0, SEAT_BOOKED: 0, "revenue": 0.0}
        for eid in event_ids
    }
    for event_id, seat_status, count in seat_rows:
        stats[event_id][seat_status] = count
    for event_id, revenue in revenue_rows:
        stats[event_id]["revenue"] = float(revenue)

    return stats


@router.post(
    "/events/draft",
    response_model=EventDraftOut,
    # AI calls are rate-limited as each draft incurs API costs.
    dependencies=[Depends(limit_user(BOOKING))],
)
def draft_event(
    payload: EventDraftRequest,
    user: User = Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN)),
):
    """
    Generate an event listing draft from a brief.

    ⚠️ This does not persist data. It returns suggestions for the organizer
    to review and publish.

    AI-generated content is not auto-published; the organizer is responsible
    for the accuracy of the event description.
    """
    if not ai.is_enabled():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AI drafting is not available — set GEMINI_API_KEY",
        )

    draft = ai.draft_event_copy(payload.brief)
    if draft is None:
        # Handle model failures gracefully; allow the organizer to input manually.
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Could not produce a draft — try adding a little more detail",
        )

    return EventDraftOut(**draft)


@router.post("/events", response_model=OrganizerEventOut, status_code=status.HTTP_201_CREATED)
def create_event(
    payload: EventCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN)),
):
    """
    Create a new event and its associated seats.

    Supports two input methods:
      - Explicit layout: Used as-is.
      - Price tiers: Layout is generated from tiers.

    Both paths converge on `seat_layout.expand()` to ensure consistent
    logic and prevent divergence.
    """
    if payload.layout is not None:
        plan_source = payload.layout.model_dump()
    else:
        total_rows = sum(tier.rows for tier in payload.price_tiers)
        if total_rows > len(ROW_LABELS):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"At most {len(ROW_LABELS)} rows are possible (A-Z), you asked for {total_rows}",
            )
        plan_source = seat_layout.from_price_tiers(
            [t.model_dump() for t in payload.price_tiers],
            payload.seats_per_row,
            ROW_LABELS,
        )

    # ⚠️ Validate before creating seats to avoid partial state on failure.
    try:
        planned = seat_layout.expand(plan_source)
    except seat_layout.LayoutError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    total_seats = len(planned)

    event = Event(
        name=payload.name,
        venue=payload.venue,
        starts_at=payload.starts_at,
        description=payload.description,
        category=payload.category,
        total_seats=total_seats,
        organizer_id=user.id,
        # Store layout to ensure consistent grid rendering.
        layout=plan_source,
        dynamic_pricing=payload.dynamic_pricing,
        demand_factor=payload.demand_factor,
        max_surge=payload.max_surge,
    )
    db.add(event)
    db.flush()      # Required for event.id

    # Use bulk_save_objects for performance over individual INSERTs.
    db.bulk_save_objects([
        Seat(
            event_id=event.id,
            section=p.section,
            row_label=p.row_label,
            seat_number=p.seat_number,
            price=p.price,
        )
        for p in planned
    ])
    db.commit()
    db.refresh(event)

    return _to_organizer_out(event, {SEAT_AVAILABLE: total_seats, SEAT_LOCKED: 0, SEAT_BOOKED: 0, "revenue": 0.0})


@router.get("/events", response_model=list[OrganizerEventOut])
def my_events(
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN)),
):
    """Retrieve events and sales stats. Admins see all events."""
    query = select(Event).order_by(Event.starts_at.desc())
    if user.role != ROLE_ADMIN:
        query = query.where(Event.organizer_id == user.id)

    events = list(db.scalars(query).all())
    stats = _event_stats(db, [e.id for e in events])

    return [_to_organizer_out(e, stats.get(e.id, {})) for e in events]


@router.patch("/events/{event_id}", response_model=OrganizerEventOut)
def update_event(
    event_id: int,
    payload: EventUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN)),
):
    """
    Update event details and pricing parameters.

    Seat layout and base prices are immutable to protect existing bookings.
    Surge parameters can be updated to affect future bookings.
    """
    event = _owned_event(event_id, user, db)
    pricing_before = (event.dynamic_pricing, event.demand_factor, event.max_surge)

    # Use exclude_unset to prevent overwriting fields with None.
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(event, field, value)

    db.commit()
    db.refresh(event)

    # Notify clients of pricing changes to prevent checkout discrepancies.
    if (event.dynamic_pricing, event.demand_factor, event.max_surge) != pricing_before:
        broadcast_pricing_update(db, event.id)

    stats = _event_stats(db, [event.id])
    return _to_organizer_out(event, stats.get(event.id, {}))


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(
    event_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN)),
):
    """
    Delete an event only if no confirmed bookings exist.

    ⚠️ Critical business rule: prevents deletion of events with active tickets.
    """
    event = _owned_event(event_id, user, db)

    confirmed = db.scalar(
        select(func.count(Booking.id)).where(
            Booking.event_id == event_id, Booking.status == BOOKING_CONFIRMED
        )
    )
    if confirmed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{confirmed} confirmed bookings exist — this event cannot be deleted",
        )

    db.delete(event)   # Seats are removed via cascade
    db.commit()


def _to_organizer_out(event: Event, stats: dict) -> OrganizerEventOut:
    return OrganizerEventOut(
        id=event.id,
        name=event.name,
        venue=event.venue,
        starts_at=event.starts_at,
        total_seats=event.total_seats,
        description=event.description,
        category=event.category,
        available_seats=stats.get(SEAT_AVAILABLE, 0),
        locked_seats=stats.get(SEAT_LOCKED, 0),
        booked_seats=stats.get(SEAT_BOOKED, 0),
        revenue=stats.get("revenue", 0.0),
        created_at=event.created_at,
    )
