"""
Booking routes.

⭐ Three-layer defense strategy:

  layer 1 — Redis lock         (fast rejection, prevents DB load)
  layer 2 — Optimistic locking (version column)
  layer 3 — Database constraint (partial unique index)

Note: Phase 3 logic was correct even without layer 1. Redis improves
performance, not correctness. This is a key architectural discussion point.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import get_current_user
from config import settings
from database import get_db
from events_broadcast import broadcast_seat_update
from idempotency import Idempotency
from job_queue import enqueue_ticket
from locking_strategies import OPTIMISTIC, PESSIMISTIC, claim_optimistic, claim_pessimistic
from pricing_state import price_now
from models import (
    BOOKING_CANCELLED,
    BOOKING_CONFIRMED,
    SEAT_AVAILABLE,
    SEAT_BOOKED,
    SEAT_LOCKED,
    TICKET_PENDING,
    TICKET_READY,
    Booking,
    Event,
    Seat,
    User,
)
from rate_limit import BOOKING, limit_user
from redis_client import acquire_seat_lock, get_lock_owner, release_seat_lock
from schemas import BookingCreate, BookingDetail, BookingOut
from tickets import ticket_path

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
    # ---- Benchmark-only knobs (Phase 15) ----
    # Ignored unless BENCHMARK_MODE is enabled.
    # include_in_schema=False hides these from public API documentation.
    strategy: str | None = Query(None, include_in_schema=False),
    redis_lock: str | None = Query(None, include_in_schema=False),
):
    """
    Book a seat.

    Returns 409 Conflict if the seat is already taken. This is expected
    behavior during high-traffic flash sales.

    User identity is derived from the token. Previously, user_id was
    accepted in the body, which posed a security risk.

    ---- Idempotency ----
    Clients can provide an `Idempotency-Key` header. Subsequent requests
    with the same key return the cached response, preventing issues
    from double-clicks or network retries.
    """
    knobs = _benchmark_knobs(strategy, redis_lock)

    idem = Idempotency(request, user.id, "booking", payload.model_dump())
    cached = idem.begin()
    if cached:
        return idem.replay(response, cached)

    try:
        booking = _perform_booking(payload, db, user, **knobs)
    except Exception:
        # Release idempotency claim on failure to allow immediate retries.
        idem.abort()
        raise

    result = BookingOut.model_validate(booking).model_dump(mode="json")
    idem.complete(result, status_code=status.HTTP_201_CREATED)
    return result


def _benchmark_knobs(strategy: str | None, redis_lock: str | None) -> dict:
    """
    Parses benchmark query parameters.

    These are silently ignored if BENCHMARK_MODE is disabled to avoid
    exposing internal measurement tools in the public API surface.
    """
    if not settings.BENCHMARK_MODE:
        return {}

    return {
        "strategy": PESSIMISTIC if strategy == PESSIMISTIC else OPTIMISTIC,
        "use_redis_lock": redis_lock != "off",
    }


def _perform_booking(
    payload: BookingCreate,
    db: Session,
    user: User,
    strategy: str = OPTIMISTIC,
    use_redis_lock: bool = True,
) -> Booking:
    """
    Core booking logic implementing the three-layer defense.

    Separated to maintain a clean idempotency wrapper and preserve
    the logic structure established in Phase 4.

    `strategy` and `use_redis_lock` are for benchmarking (Phase 15).
    Benchmarks must execute this function directly to ensure production
    parity.
    """
    seat = db.get(Seat, payload.seat_id)
    if seat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Seat not found")

    if seat.status == SEAT_BOOKED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Seat already booked")

    # ---- LAYER 1: REDIS LOCK ----
    # Handles two scenarios:
    #   a) User previously selected the seat (lock already held).
    #   b) Direct POST request (lock acquired here).
    #
    # Redis prevents database contention by rejecting unauthorized
    # requests before they reach the DB.
    lock_owner = get_lock_owner(payload.seat_id) if use_redis_lock else None
    lock_taken_here = False

    if not use_redis_lock:
        # Benchmark: Redis layer disabled to stress-test DB locking strategies.
        pass
    elif lock_owner is None:
        if not acquire_seat_lock(payload.seat_id, user.id):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Seat currently held by another user"
            )
        lock_taken_here = True
    elif lock_owner != user.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Seat is held by another user"
        )

    if seat.status not in (SEAT_AVAILABLE, SEAT_LOCKED):
        if lock_taken_here:
            release_seat_lock(payload.seat_id, user.id)
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Seat not available (status: {seat.status})"
        )

    expected_version = seat.version
    # Use `price_now` to retrieve the locked price or current dynamic price.
    # Never use `seat.price` directly as it is the base price.
    amount = price_now(db, seat)
    event_id = seat.event_id

    # ---- LAYER 2: DATABASE-LEVEL CLAIM ----
    #
    # Optimistic locking is default for production. Pessimistic is
    # reserved for benchmarks (Phase 15).
    #
    # Optimistic is preferred because it avoids holding DB connections
    # during contention, preventing connection pool exhaustion.
    if strategy == PESSIMISTIC:
        claim = claim_pessimistic(db, payload.seat_id)
    else:
        claim = claim_optimistic(db, payload.seat_id, expected_version)

    if not claim.won:
        db.rollback()
        if lock_taken_here:
            release_seat_lock(payload.seat_id, user.id)
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Seat was just booked by another user"
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
    # Final safety net: the partial unique index prevents duplicate
    # confirmed bookings even if application logic fails.
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if lock_taken_here:
            release_seat_lock(payload.seat_id, user.id)
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Booking for this seat already exists"
        )

    release_seat_lock(payload.seat_id, user.id)

    # Notify clients to update UI grid.
    broadcast_seat_update(db, payload.seat_id, "booked")

    # Offload ticket generation to background worker.
    enqueue_ticket(booking.id)

    db.refresh(booking)
    return booking


@router.get("", response_model=list[BookingDetail])
def list_bookings(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Retrieve user's bookings, sorted by recency.
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
            ticket_status=b.ticket_status,
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
    Cancel booking and release seat.

    The record is preserved for history; only the status is updated.
    """
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")

    # ⚠️ Ownership check prevents IDOR vulnerabilities.
    # Returns 404 instead of 403 to avoid leaking existence of the booking.
    if booking.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")

    if booking.status == BOOKING_CANCELLED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Booking already cancelled")

    booking.status = BOOKING_CANCELLED

    # Increment version to ensure consistency for concurrent requests.
    db.execute(
        update(Seat)
        .where(Seat.id == booking.seat_id)
        .values(status=SEAT_AVAILABLE, version=Seat.version + 1, locked_by=None, locked_until=None)
        .execution_options(synchronize_session=False)
    )

    db.commit()

    broadcast_seat_update(db, booking.seat_id, "cancelled")

    db.refresh(booking)
    return booking


@router.get("/{booking_id}/ticket")
def download_ticket(
    booking_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Download ticket PDF.

    ⚠️ Ownership check prevents unauthorized access to tickets.
    """
    booking = db.get(Booking, booking_id)
    if booking is None or booking.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")

    if booking.status != BOOKING_CONFIRMED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot download ticket for cancelled booking")

    if booking.ticket_status != TICKET_READY:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Ticket not ready (status: {booking.ticket_status})",
        )

    path = ticket_path(booking.id)
    if not path.exists():
        # Re-queue if file is missing from storage.
        booking.ticket_status = TICKET_PENDING
        db.commit()
        enqueue_ticket(booking.id)
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Ticket file missing — regenerating"
        )

    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"SeatPulse-SP{booking.id:05d}.pdf",
    )


@router.post("/{booking_id}/ticket/retry", response_model=BookingOut)
def retry_ticket(
    booking_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Retry failed ticket generation."""
    booking = db.get(Booking, booking_id)
    if booking is None or booking.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")

    if booking.ticket_status == TICKET_READY:
        return booking

    booking.ticket_status = TICKET_PENDING
    db.commit()
    enqueue_ticket(booking.id)

    db.refresh(booking)
    return booking
