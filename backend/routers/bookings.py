"""
Bookings ke routes.

⭐ Yahan pehli baar concurrency handling actually chal rahi hai.

Teen layer me se do abhi active hain:
  layer 2 — optimistic locking (version column)
  layer 3 — database constraints (partial unique index)

Layer 1 (Redis fast-rejection) Phase 4 me iske upar aayegi. Notice karna:
Redis aane par bhi ye code waisa hi rahega — Redis sirf ek fast filter hai
jo zyadatar requests ko yahan tak pahunchne hi nahi deta.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from models import (
    BOOKING_CANCELLED,
    BOOKING_CONFIRMED,
    SEAT_AVAILABLE,
    SEAT_BOOKED,
    Booking,
    Event,
    Seat,
    User,
)
from schemas import BookingCreate, BookingDetail, BookingOut

router = APIRouter(prefix="/api/bookings", tags=["bookings"])


@router.post("", response_model=BookingOut, status_code=status.HTTP_201_CREATED)
def create_booking(payload: BookingCreate, db: Session = Depends(get_db)):
    """
    Seat book karo.

    409 Conflict tab milta hai jab koi aur pehle book kar chuka ho.
    Ye error normal hai — flash sale me yahi sabse zyada return hoga.
    """
    seat = db.get(Seat, payload.seat_id)
    if seat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Seat nahi mili")

    if db.get(User, payload.user_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User nahi mila")

    # Pehla check — sasta hai, saaf error message deta hai.
    # Par ye kaafi NAHI hai: is line aur neeche wale UPDATE ke beech me
    # koi dusra request seat le sakta hai. Asli guarantee UPDATE me hai.
    if seat.status != SEAT_AVAILABLE:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Seat available nahi hai (status: {seat.status})"
        )

    expected_version = seat.version
    amount = float(seat.price)
    event_id = seat.event_id

    # ---- LAYER 2: OPTIMISTIC LOCKING ----
    # Poora faisla is ek atomic UPDATE me hota hai.
    # WHERE me version aur status dono hain — do parallel requests me
    # sirf ek ka WHERE match karega, dusre ko 0 rows milengi.
    result = db.execute(
        update(Seat)
        .where(
            Seat.id == payload.seat_id,
            Seat.version == expected_version,
            Seat.status == SEAT_AVAILABLE,
        )
        .values(status=SEAT_BOOKED, version=Seat.version + 1)
        .execution_options(synchronize_session=False)
    )

    if result.rowcount == 0:
        # Koi aur jeet gaya. Hamara version purana ho chuka hai.
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Seat abhi abhi kisi aur ne book kar li"
        )

    booking = Booking(
        user_id=payload.user_id,
        seat_id=payload.seat_id,
        event_id=event_id,
        status=BOOKING_CONFIRMED,
        amount=amount,
    )
    db.add(booking)

    # ---- LAYER 3: DATABASE CONSTRAINT ----
    # Upar wala UPDATE 99.9% case pakad leta hai. Ye aakhri jaal hai —
    # partial unique index ek seat ki dusri confirmed booking insert hone hi
    # nahi dega, chahe upar ka logic kisi bug ki wajah se fail ho jaye.
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Is seat ki booking pehle se maujood hai"
        )

    db.refresh(booking)
    return booking


@router.get("", response_model=list[BookingDetail])
def list_bookings(user_id: int, db: Session = Depends(get_db)):
    """Ek user ki saari bookings, nayi pehle."""
    rows = db.execute(
        select(Booking, Seat, Event)
        .join(Seat, Seat.id == Booking.seat_id)
        .join(Event, Event.id == Booking.event_id)
        .where(Booking.user_id == user_id)
        .order_by(Booking.created_at.desc())
    ).all()

    return [
        BookingDetail(
            id=b.id,
            user_id=b.user_id,
            seat_id=b.seat_id,
            event_id=b.event_id,
            status=b.status,
            amount=float(b.amount),
            created_at=b.created_at,
            seat_label=f"{s.row_label}-{s.seat_number}",
            event_name=e.name,
        )
        for b, s, e in rows
    ]


@router.delete("/{booking_id}", response_model=BookingOut)
def cancel_booking(booking_id: int, db: Session = Depends(get_db)):
    """
    Booking cancel karo aur seat wapas available karo.

    Note: booking row delete nahi kar rahe, sirf status badal rahe hain.
    Partial unique index sirf 'confirmed' par lagta hai, isliye cancelled
    hone ke baad wahi seat dubara bik sakti hai — aur record bhi bacha rehta hai.
    """
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking nahi mili")

    if booking.status == BOOKING_CANCELLED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Booking pehle se cancelled hai")

    booking.status = BOOKING_CANCELLED

    # Seat wapas available. Yahan bhi version badhana zaroori hai — koi aur
    # request jo purana version leke baithi hai, wo ab galat data pe kaam na kare.
    db.execute(
        update(Seat)
        .where(Seat.id == booking.seat_id)
        .values(status=SEAT_AVAILABLE, version=Seat.version + 1, locked_by=None, locked_until=None)
        .execution_options(synchronize_session=False)
    )

    db.commit()
    db.refresh(booking)
    return booking
