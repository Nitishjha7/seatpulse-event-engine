"""
Seat routes and Redis distributed locking.

Flow: Select seat -> acquire lock (5 min) -> pay -> book.
If the lock is not released, the Redis TTL will handle it automatically.
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
    Revert expired 'locked' seats in the DB to 'available'.

    Reason:
      Redis is the source of truth for locks. When a Redis key TTL expires,
      it is deleted silently without notifying Postgres. Consequently,
      seats remain 'locked' in the DB even after they are free.

    We perform a lightweight UPDATE before reading seats. This is a
    "lazy cleanup" strategy, avoiding the need for background jobs or cron.
    """
    # Include payment_pending; abandoned checkouts must release the seat,
    # otherwise, an abandoned payment would block the seat indefinitely.
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
            # If the hold expires, the price lock is also released. The next
            # attempt will use the current (potentially higher) price. This
            # prevents users from holding a seat to lock in an old price.
            held_price=None,
            version=Seat.version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()

    # Broadcast expiration so other tabs update immediately.
    for seat_id in expired:
        broadcast_seat_update(db, seat_id, "expired")


@router.get("/events/{event_id}/seats", response_model=list[SeatOut])
def list_event_seats(event_id: int, db: Session = Depends(get_db)):
    """
    Retrieve all seats for an event to build the seat grid.

    Sorted by row and number to minimize frontend processing.
    """
    if db.get(Event, event_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    release_expired_locks(db, event_id)

    seats = db.scalars(
        select(Seat)
        .where(Seat.event_id == event_id)
        .order_by(Seat.row_label, Seat.seat_number)
    ).all()

    # Calculate pricing once per event rather than per seat to avoid
    # excessive queries for large venues.
    info = pricing_state(db, db.get(Event, event_id))
    return [_seat_out(seat, info) for seat in seats]


def _seat_out(seat: Seat, info) -> SeatOut:
    """Map ORM Seat to API SeatOut with current price."""
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Seat not found")
    return _seat_out(seat, pricing_state(db, db.get(Event, seat.event_id)))


@router.post(
    "/seats/{seat_id}/lock",
    response_model=SeatLockOut,
    # ⭐ High-traffic endpoint; target for bots.
    # 15 burst allowed (for users selecting multiple seats), then 5/s.
    dependencies=[Depends(limit_user(SEAT_LOCK))],
)
def lock_seat(
    seat_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    ⭐ Hold a seat.

    This is a high-concurrency endpoint. Decisions are made via atomic
    Redis commands before hitting the database.

    User identity is derived from the token.
    """
    seat = db.get(Seat, seat_id)
    if seat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Seat not found")

    # Cannot lock already booked seats.
    if seat.status not in (SEAT_AVAILABLE, SEAT_LOCKED):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Seat not available (status: {seat.status})"
        )

    # ---- LAYER 1: REDIS ATOMIC LOCK ----
    # SET seat:42:lock <user_id> NX EX 300
    # Only one request will succeed among thousands.
    if not acquire_seat_lock(seat_id, user.id):
        owner = get_lock_owner(seat_id)

        # If the user already owns the lock, return the remaining TTL.
        if owner == user.id:
            return SeatLockOut(
                seat_id=seat_id,
                locked_by=owner,
                expires_in=get_lock_ttl(seat_id),
                already_owned=True,
            )

        raise HTTPException(
            status.HTTP_409_CONFLICT, "Seat is already held by another user"
        )

    # Lock acquired. Update DB so other users see the seat as 'locked' in the grid.
    #
    # ⚠️ The WHERE clause status check is CRITICAL to prevent race conditions:
    #
    #   B: read seat (locked by A)      -> check passes
    #   A: book seat                    -> status=booked, Redis lock released
    #   B: acquire Redis lock (free)    -> updates DB status=locked
    #      ...result: 'booked' seat is overwritten to 'locked'.
    #
    # With the guard, if the seat was booked in the interim, rowcount is 0,
    # we release the Redis lock and return 409.
    ttl = settings.SEAT_LOCK_TTL

    # PRICE LOCK -- freeze the price at the time of the hold.
    #
    # This ensures the user pays the price they saw in the grid, regardless
    # of dynamic pricing changes during the hold.
    #
    # This is stored as a column because historical demand cannot be
    # recomputed accurately later.
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
            status.HTTP_409_CONFLICT, "Seat was booked just now"
        )

    db.commit()

    # Notify connected clients to update the grid.
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
    Release a lock (e.g., user selected a different seat or cancelled).

    Lua script ensures only the owner can release the lock.
    """
    if db.get(Seat, seat_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Seat not found")

    released = release_seat_lock(seat_id, user.id)

    if released:
        db.execute(
            update(Seat)
            .where(Seat.id == seat_id, Seat.status == SEAT_LOCKED)
            .values(
                status=SEAT_AVAILABLE,
                locked_by=None,
                locked_until=None,
                held_price=None,   # Release price lock
                version=Seat.version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        broadcast_seat_update(db, seat_id, "released")

    # released=False is normal if the lock expired via TTL.
    return SeatLockOut(
        seat_id=seat_id,
        locked_by=None,
        expires_in=0,
        already_owned=False,
        released=released,
    )


@router.get("/seats/{seat_id}/lock", response_model=SeatLockOut)
def get_seat_lock(seat_id: int):
    """Check lock ownership and remaining TTL. Useful for debugging."""
    owner = get_lock_owner(seat_id)
    return SeatLockOut(
        seat_id=seat_id,
        locked_by=owner,
        expires_in=get_lock_ttl(seat_id),
        already_owned=False,
    )
