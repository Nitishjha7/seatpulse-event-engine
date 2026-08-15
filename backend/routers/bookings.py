"""
Bookings ke routes.

⭐ Teeno defence layers yahan ek saath chal rahi hain:

  layer 1 — Redis lock         (fast rejection, DB tak load hi nahi aata)
  layer 2 — optimistic locking (version column)
  layer 3 — database constraint (partial unique index)

Notice karna: Phase 3 ka code layer 2 aur 3 ke saath bhi SAHI tha.
Redis ne correctness nahi badli — usne sirf speed di. Isi baat par
interview me sabse zyada baat hoti hai.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from events_broadcast import broadcast_seat_update
from idempotency import Idempotency
from models import (
    BOOKING_CANCELLED,
    BOOKING_CONFIRMED,
    SEAT_AVAILABLE,
    SEAT_BOOKED,
    SEAT_LOCKED,
    Booking,
    Event,
    Seat,
    User,
)
from rate_limit import BOOKING, limit_user
from redis_client import acquire_seat_lock, get_lock_owner, release_seat_lock
from schemas import BookingCreate, BookingDetail, BookingOut

router = APIRouter(prefix="/api/bookings", tags=["bookings"])


@router.post(
    "",
    response_model=BookingOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limit_user(BOOKING))],
)
def create_booking(
    payload: BookingCreate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Seat book karo.

    409 Conflict tab milta hai jab koi aur pehle book kar chuka ho.
    Ye error normal hai — flash sale me yahi sabse zyada return hoga.

    User token se aata hai. Pehle body me `user_id` jata tha — koi bhi
    kisi aur ke naam booking kar sakta tha.

    ---- Idempotency ----
    Client `Idempotency-Key` header bhej sakta hai. Wahi key dubara aayi
    to naya kaam nahi hota — pehla jawab wapas milta hai. Double-click
    aur network retry dono isse safe ho jaate hain.
    """
    idem = Idempotency(request, user.id, "booking", payload.model_dump())
    cached = idem.begin()
    if cached:
        return idem.replay(response, cached)

    try:
        booking = _perform_booking(payload, db, user)
    except Exception:
        # Fail hua to idempotency claim chhod do — warna user usi key se
        # 60 second tak retry hi nahi kar payega
        idem.abort()
        raise

    result = BookingOut.model_validate(booking).model_dump(mode="json")
    idem.complete(result, status_code=status.HTTP_201_CREATED)
    return result


def _perform_booking(payload: BookingCreate, db: Session, user: User) -> Booking:
    """
    Asli booking logic — teeno defence layers.

    Alag function isliye taki upar idempotency ka wrapper saaf dikhe aur
    ye logic bilkul waisa ka waisa rahe jaisa Phase 4 me tha.
    """
    seat = db.get(Seat, payload.seat_id)
    if seat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Seat nahi mili")

    if seat.status == SEAT_BOOKED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Seat pehle se booked hai")

    # ---- LAYER 1: REDIS LOCK ----
    # Do raaste yahan aate hain:
    #   a) User ne pehle seat select ki thi -> lock uske paas hai (normal UI flow)
    #   b) Koi seedha POST /api/bookings maar raha hai -> yahin lock lete hain
    #
    # Dono case me booking Redis lock ke bina aage nahi badhti. Isliye
    # 5000 parallel requests me se 4999 yahin ruk jaati hain — unka
    # database se koi wasta hi nahi padta.
    lock_owner = get_lock_owner(payload.seat_id)
    lock_taken_here = False

    if lock_owner is None:
        if not acquire_seat_lock(payload.seat_id, user.id):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Seat abhi kisi aur ne hold kar li"
            )
        lock_taken_here = True
    elif lock_owner != user.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Ye seat kisi aur ke paas hold hai"
        )

    # Seat locked hai par lock kisi aur ka — upar handle ho chuka.
    # Yahan tak aaye matlab seat available hai ya HAMARE lock me hai.
    if seat.status not in (SEAT_AVAILABLE, SEAT_LOCKED):
        if lock_taken_here:
            release_seat_lock(payload.seat_id, user.id)
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
            Seat.status.in_((SEAT_AVAILABLE, SEAT_LOCKED)),
        )
        .values(
            status=SEAT_BOOKED,
            version=Seat.version + 1,
            locked_by=None,
            locked_until=None,
        )
        .execution_options(synchronize_session=False)
    )

    if result.rowcount == 0:
        # Koi aur jeet gaya. Hamara version purana ho chuka hai.
        db.rollback()
        if lock_taken_here:
            release_seat_lock(payload.seat_id, user.id)
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Seat abhi abhi kisi aur ne book kar li"
        )

    booking = Booking(
        user_id=user.id,
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
        if lock_taken_here:
            release_seat_lock(payload.seat_id, user.id)
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Is seat ki booking pehle se maujood hai"
        )

    # Booking ho gayi — ab lock ki zaroorat nahi. Seat permanently 'booked' hai,
    # Redis me key pade rehne ka koi faayda nahi.
    release_seat_lock(payload.seat_id, user.id)

    # Sab clients ko batao — unke grid me seat turant laal ho jayegi
    broadcast_seat_update(db, payload.seat_id, "booked")

    db.refresh(booking)
    return booking


@router.get("", response_model=list[BookingDetail])
def list_bookings(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    MERI bookings, nayi pehle.

    Pehle `?user_id=` query param leta tha — matlab koi bhi kisi ki
    bookings dekh sakta tha. Ab sirf apni.
    """
    rows = db.execute(
        select(Booking, Seat, Event)
        .join(Seat, Seat.id == Booking.seat_id)
        .join(Event, Event.id == Booking.event_id)
        .where(Booking.user_id == user.id)
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
def cancel_booking(
    booking_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Apni booking cancel karo aur seat wapas available karo.

    Note: booking row delete nahi kar rahe, sirf status badal rahe hain.
    Partial unique index sirf 'confirmed' par lagta hai, isliye cancelled
    hone ke baad wahi seat dubara bik sakti hai — aur record bhi bacha rehta hai.
    """
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking nahi mili")

    # ⚠️ Ownership check. Bina iske koi bhi /api/bookings/1, /2, /3 chala ke
    # dusron ki bookings cancel kar deta (IDOR — sabse common API bug).
    #
    # 404 de rahe hain, 403 nahi: 403 se attacker ko pata chal jata ki wo
    # booking exist karti hai. 404 kuch nahi batata.
    if booking.user_id != user.id:
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

    # Seat wapas available — sab clients ke grid me turant hari ho jayegi
    broadcast_seat_update(db, booking.seat_id, "cancelled")

    db.refresh(booking)
    return booking
