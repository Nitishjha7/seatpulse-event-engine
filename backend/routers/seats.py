"""
Seats ke routes + Redis distributed locking.

Flow: seat select karo -> lock milta hai (5 min) -> pay karo -> book.
Lock na chhoda? Redis TTL khud release kar dega.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from auth import get_current_user
from config import settings
from database import get_db
from events_broadcast import broadcast_seat_update
from models import (
    SEAT_AVAILABLE,
    SEAT_LOCKED,
    SEAT_PAYMENT_PENDING,
    Event,
    Seat,
    User,
    utcnow,
)
from pricing import current_price
from pricing_state import pricing_state
from rate_limit import SEAT_LOCK, limit_user
from redis_client import (
    acquire_seat_lock,
    get_lock_owner,
    get_lock_ttl,
    release_seat_lock,
)
from schemas import SeatLockOut, SeatOut

router = APIRouter(prefix="/api", tags=["seats"])


def release_expired_locks(db: Session, event_id: int) -> None:
    """
    DB me pade purane 'locked' seats ko wapas 'available' karo.

    Zaroorat kyu:
      Lock ka asli maalik Redis hai, aur Redis key TTL par CHUPCHAP delete ho
      jaati hai — wo Postgres ko batane nahi aata. To DB me seat 'locked' hi
      padi reh jati hai jabki asal me free ho chuki hai.

    Isliye seats padhne se pehle ek sasta UPDATE chala dete hain.
    Ye "lazy cleanup" hai — background job/cron ki zaroorat nahi.
    """
    # payment_pending bhi shaamil hai — abandoned checkout ki seat bhi
    # wapas aani chahiye, warna ek chhoda hua payment seat hamesha block kar deta.
    expired = db.scalars(
        select(Seat.id).where(
            Seat.event_id == event_id,
            Seat.status.in_((SEAT_LOCKED, SEAT_PAYMENT_PENDING)),
            Seat.locked_until < utcnow(),
        )
    ).all()

    if not expired:
        return

    db.execute(
        update(Seat)
        .where(Seat.id.in_(expired))
        .values(
            status=SEAT_AVAILABLE,
            locked_by=None,
            locked_until=None,
            # Hold gaya to price lock bhi gaya -- agli baar naya (shayad
            # zyada) price lagega. Ye saaf karna zaroori hai, warna user
            # ek baar hold karke hamesha ke liye purana price pa leta.
            held_price=None,
            version=Seat.version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()

    # Expire hui seats ka bhi broadcast — dusre tabs me wo turant hari ho jayengi
    for seat_id in expired:
        broadcast_seat_update(db, seat_id, "expired")


@router.get("/events/{event_id}/seats", response_model=list[SeatOut])
def list_event_seats(event_id: int, db: Session = Depends(get_db)):
    """
    Ek event ki saari seats — seat grid isi se banta hai.

    Row aur number se sorted, taki frontend ko sort na karna pade.
    """
    if db.get(Event, event_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event nahi mila")

    release_expired_locks(db, event_id)

    seats = db.scalars(
        select(Seat)
        .where(Seat.event_id == event_id)
        .order_by(Seat.row_label, Seat.seat_number)
    ).all()

    # Pricing EK BAAR nikalte hain, har seat ke liye nahi. Multiplier poore
    # event ka ek hi hota hai -- 500 seats ke liye 500 count queries maarna
    # bewakoofi hoti.
    info = pricing_state(db, db.get(Event, event_id))
    return [_seat_out(seat, info) for seat in seats]


def _seat_out(seat: Seat, info) -> SeatOut:
    """ORM Seat -> API SeatOut, current price ke saath."""
    return SeatOut(
        id=seat.id,
        event_id=seat.event_id,
        row_label=seat.row_label,
        seat_number=seat.seat_number,
        section=seat.section,
        price=float(seat.price),
        status=seat.status,
        version=seat.version,
        locked_by=seat.locked_by,
        locked_until=seat.locked_until,
        current_price=current_price(float(seat.price), info),
        held_price=float(seat.held_price) if seat.held_price is not None else None,
    )


@router.get("/seats/{seat_id}", response_model=SeatOut)
def get_seat(seat_id: int, db: Session = Depends(get_db)):
    seat = db.get(Seat, seat_id)
    if seat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Seat nahi mili")
    return _seat_out(seat, pricing_state(db, db.get(Event, seat.event_id)))


@router.post(
    "/seats/{seat_id}/lock",
    response_model=SeatLockOut,
    # ⭐ Flash sale ka sabse garam endpoint — bots yahi hammer karte hain.
    # 15 burst allowed (user 4-5 seats jaldi try kar sakta hai), phir 5/s.
    dependencies=[Depends(limit_user(SEAT_LOCK))],
)
def lock_seat(
    seat_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    ⭐ Seat ko apne naam hold karo.

    Ye flash sale ka sabse garam endpoint hai — 5000 log ek saath yahi hit
    karte hain. Isliye poora faisla Redis ke ek atomic command me hota hai,
    database tak baat pahunchne se pehle.

    User token se aata hai — request body me user_id nahi bheja ja sakta.
    """
    seat = db.get(Seat, seat_id)
    if seat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Seat nahi mili")

    # Pehle se booked seat pe lock ka koi matlab nahi
    if seat.status not in (SEAT_AVAILABLE, SEAT_LOCKED):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Seat available nahi hai (status: {seat.status})"
        )

    # ---- LAYER 1: REDIS ATOMIC LOCK ----
    # SET seat:42:lock <user_id> NX EX 300
    # 5000 requests me se theek EK ko True milega.
    if not acquire_seat_lock(seat_id, user.id):
        owner = get_lock_owner(seat_id)

        # Apna hi lock dubara maanga? Theek hai, TTL bata do.
        if owner == user.id:
            return SeatLockOut(
                seat_id=seat_id,
                locked_by=owner,
                expires_in=get_lock_ttl(seat_id),
                already_owned=True,
            )

        raise HTTPException(
            status.HTTP_409_CONFLICT, "Ye seat abhi kisi aur ne hold ki hui hai"
        )

    # Lock mil gaya. Ab DB me bhi likh do — sirf isliye taki DUSRE users ko
    # grid me ye seat peeli dikhe. Asli lock Redis me hi hai.
    #
    # ⚠️ WHERE me status ka check ZAROORI hai. Ye race load test me pakdi gayi thi:
    #
    #   B: seat padhi (locked by A)      -> upar wala check pass ho gaya
    #   A: book kar li                    -> status=booked, Redis lock release
    #   B: Redis lock mil gaya (free tha) -> aur DB me status=locked likh diya
    #      ...matlab 'booked' seat wapas 'locked' ho gayi. Ek confirmed booking
    #      thi, par seat booked nahi dikhti thi.
    #
    # Guard ke saath: seat beech me book ho gayi to rowcount 0 aata hai,
    # hum lock wapas chhod dete hain aur 409 dete hain.
    ttl = settings.SEAT_LOCK_TTL

    # PRICE LOCK -- hold ke saath price bhi freeze ho jata hai.
    #
    # User ko grid me jo price dikha tha, checkout pe wahi lagega. Beech me
    # 50 seats bik jayein to bhi is user ka price nahi badlega.
    #
    # Ye jaan-boojh ke ek COLUMN hai, calculation nahi -- kyunki "us waqt
    # kya price tha" ko baad me dobara compute nahi kiya ja sakta. Demand
    # tab tak badal chuki hoti hai.
    #
    # Note: seat pehle se `locked` thi aur wahi user dubara lock kar raha
    # hai, to bhi naya price likh dete hain -- TTL bhi to reset ho raha hai,
    # matlab ye ek naya hold hai.
    quoted = current_price(
        float(seat.price), pricing_state(db, db.get(Event, seat.event_id))
    )

    result = db.execute(
        update(Seat)
        .where(
            Seat.id == seat_id,
            Seat.status.in_((SEAT_AVAILABLE, SEAT_LOCKED)),
        )
        .values(
            status=SEAT_LOCKED,
            locked_by=user.id,
            locked_until=utcnow() + timedelta(seconds=ttl),
            held_price=quoted,
            version=Seat.version + 1,
        )
        .execution_options(synchronize_session=False)
    )

    if result.rowcount == 0:
        db.rollback()
        release_seat_lock(seat_id, user.id)
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Seat abhi abhi book ho gayi"
        )

    db.commit()

    # Sab connected clients ko batao — unke grid me ye seat turant peeli ho jayegi
    broadcast_seat_update(db, seat_id, "locked")

    return SeatLockOut(
        seat_id=seat_id,
        locked_by=user.id,
        expires_in=ttl,
        already_owned=False,
        price=quoted,
    )


@router.delete("/seats/{seat_id}/lock", response_model=SeatLockOut)
def unlock_seat(
    seat_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Apna lock chhodo (user ne dusri seat chun li, ya cancel kar diya).

    Lua script check karta hai ki lock hamara hi hai. Kisi aur ka lock
    galti se delete nahi hoga.
    """
    if db.get(Seat, seat_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Seat nahi mili")

    released = release_seat_lock(seat_id, user.id)

    if released:
        db.execute(
            update(Seat)
            .where(Seat.id == seat_id, Seat.status == SEAT_LOCKED)
            .values(
                status=SEAT_AVAILABLE,
                locked_by=None,
                locked_until=None,
                held_price=None,   # price lock bhi chhoda
                version=Seat.version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        broadcast_seat_update(db, seat_id, "released")

    # released=False bhi normal hai — lock TTL pe khud expire ho chuka hoga.
    # Isliye error nahi de rahe.
    return SeatLockOut(
        seat_id=seat_id,
        locked_by=None,
        expires_in=0,
        already_owned=False,
        released=released,
    )


@router.get("/seats/{seat_id}/lock", response_model=SeatLockOut)
def get_seat_lock(seat_id: int):
    """Lock kiske paas hai aur kitna time bacha hai. Debugging me kaam aata hai."""
    owner = get_lock_owner(seat_id)
    return SeatLockOut(
        seat_id=seat_id,
        locked_by=owner,
        expires_in=get_lock_ttl(seat_id),
        already_owned=False,
    )
