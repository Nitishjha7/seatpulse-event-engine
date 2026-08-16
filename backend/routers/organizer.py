"""
Organizer routes — apne events banao aur manage karo.

Yahan pehli baar RBAC kaam me aa raha hai: ye saare endpoints sirf
`organizer` ya `admin` role wale users ke liye hain.

Do alag cheezein hain jo log mila dete hain:

  AUTHENTICATION  — tum kaun ho?        (Phase 7, token se)
  AUTHORIZATION   — tum kya kar sakte ho? (ye phase, role se)

Aur ek teesri jo aur bhi zaroori hai:

  OWNERSHIP       — ye cheez TUMHARI hai?

Sirf role check kaafi nahi hota. Organizer role hone ka matlab ye nahi
ki tum KISI BHI event ko edit kar sakte ho — sirf apne wale ko.
"""

import string

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from auth import require_role
from database import get_db
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
from schemas import EventCreate, EventUpdate, OrganizerEventOut

router = APIRouter(prefix="/api/organizer", tags=["organizer"])

# Ek event me max seats. Bina limit ke koi 26 rows × 50 seats × 100 events
# bana ke database bhar sakta hai.
MAX_SEATS_PER_EVENT = 2000

ROW_LABELS = string.ascii_uppercase   # A..Z


def _owned_event(event_id: int, user: User, db: Session) -> Event:
    """
    Event lao — par sirf tabhi jab wo IS user ka ho (ya user admin ho).

    ⚠️ Ye check har organizer endpoint me chahiye. Bina iske koi bhi
    organizer `/api/organizer/events/5` chala ke kisi aur ka event edit
    kar deta — role check pass ho jata, par ownership fail hoti.
    """
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event nahi mila")

    if user.role != ROLE_ADMIN and event.organizer_id != user.id:
        # 404, 403 nahi — attacker ko ye bhi na pata chale ki event exist
        # karta hai. Wahi pattern jo bookings ke IDOR fix me use kiya tha.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event nahi mila")

    return event


def _event_stats(db: Session, event_ids: list[int]) -> dict[int, dict]:
    """
    Kai events ke counts ek saath.

    ⚠️ N+1 se bachne ke liye ek hi query. 20 events ke liye 20 alag
    queries maarna sabse common performance bug hai.
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


@router.post("/events", response_model=OrganizerEventOut, status_code=status.HTTP_201_CREATED)
def create_event(
    payload: EventCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN)),
):
    """
    Naya event + uski saari seats banao.

    Seats yahin generate hoti hain price tiers se. Organizer ko har seat
    alag se nahi banani padti — wo "3 rows @ ₹2500, 7 rows @ ₹800" bolta
    hai aur 100 seats ban jaati hain.
    """
    total_rows = sum(tier.rows for tier in payload.price_tiers)

    if total_rows > len(ROW_LABELS):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Max {len(ROW_LABELS)} rows ho sakti hain (A-Z), tumne {total_rows} maangi",
        )

    total_seats = total_rows * payload.seats_per_row
    if total_seats > MAX_SEATS_PER_EVENT:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Max {MAX_SEATS_PER_EVENT} seats — tumne {total_seats} maangi",
        )

    event = Event(
        name=payload.name,
        venue=payload.venue,
        starts_at=payload.starts_at,
        description=payload.description,
        category=payload.category,
        total_seats=total_seats,
        organizer_id=user.id,
    )
    db.add(event)
    db.flush()      # id chahiye seats banane ke liye

    # Tiers upar se neeche: pehla tier row A se
    seats = []
    row_index = 0
    for tier in payload.price_tiers:
        for _ in range(tier.rows):
            label = ROW_LABELS[row_index]
            seats.extend(
                Seat(
                    event_id=event.id,
                    row_label=label,
                    seat_number=n,
                    price=tier.price,
                )
                for n in range(1, payload.seats_per_row + 1)
            )
            row_index += 1

    # bulk_save_objects — 2000 alag INSERT se bahut tez
    db.bulk_save_objects(seats)
    db.commit()
    db.refresh(event)

    return _to_organizer_out(event, {SEAT_AVAILABLE: total_seats, SEAT_LOCKED: 0, SEAT_BOOKED: 0, "revenue": 0.0})


@router.get("/events", response_model=list[OrganizerEventOut])
def my_events(
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN)),
):
    """Mere events + sales. Admin ko sab dikhte hain."""
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
    Event ki details badlo.

    Seat layout aur pricing yahan nahi badalte — log tickets khareed
    chuke ho sakte hain. (EventUpdate schema me wo fields hain hi nahi.)
    """
    event = _owned_event(event_id, user, db)

    # exclude_unset — sirf wahi fields update ho jo client ne BHEJI hain.
    # Bina iske None bheja hua field bhi NULL kar deta.
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(event, field, value)

    db.commit()
    db.refresh(event)

    stats = _event_stats(db, [event.id])
    return _to_organizer_out(event, stats.get(event.id, {}))


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(
    event_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN)),
):
    """
    Event delete karo — par sirf tab jab koi confirmed booking na ho.

    ⚠️ Ye sabse important business rule hai. Cascade delete laga hua hai,
    to bina is check ke ek DELETE se logon ki khareedi hui tickets gayab
    ho jaatin.
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
            f"{confirmed} confirmed booking hain — event delete nahi ho sakta",
        )

    db.delete(event)   # seats cascade se chali jaayengi
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
