"""
Gate check-in — Validate entry by scanning QR codes.

⭐ This follows the same "exactly once" logic used in seat booking:

    Seat booking  : one seat, one confirmed booking
    Check-in      : one ticket, one entry

The solution is an atomic conditional UPDATE. If two gates scan the same QR
simultaneously, only one will result in a `rowcount` of 1.

Why this matters: If two people use a screenshot of the same QR at different
gates, both should not be allowed entry. This prevents common ticketing fraud.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from auth import require_role
from database import get_db
from models import (
    BOOKING_CONFIRMED,
    ROLE_ADMIN,
    ROLE_ORGANIZER,
    TICKET_READY,
    Booking,
    Event,
    Seat,
    User,
    utcnow,
)
from rate_limit import SEAT_LOCK, limit_user
from schemas import CheckInRequest, CheckInResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/checkin", tags=["checkin"])


def _result(
    *,
    ok: bool,
    reason: str,
    booking: Booking | None = None,
    seat: Seat | None = None,
    event: Event | None = None,
    attendee: User | None = None,
    scanned_by: User | None = None,
) -> CheckInResult:
    return CheckInResult(
        ok=ok,
        reason=reason,
        booking_id=booking.id if booking else None,
        booking_ref=f"SP{booking.id:05d}" if booking else None,
        seat_label=f"{seat.row_label}-{seat.seat_number}" if seat else None,
        event_name=event.name if event else None,
        attendee_name=(attendee.full_name or attendee.email) if attendee else None,
        checked_in_at=booking.checked_in_at if booking else None,
        already_checked_in=(reason == "already_checked_in"),
        scanned_by=(scanned_by.full_name or scanned_by.email) if scanned_by else None,
    )


@router.post(
    "",
    response_model=CheckInResult,
    # Allow burst scanning for staff, but prevent brute-force attempts.
    dependencies=[Depends(limit_user(SEAT_LOCK))],
)
def check_in(
    payload: CheckInRequest,
    db: Session = Depends(get_db),
    staff: User = Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN)),
):
    """
    Scan QR token and mark entry.

    ⚠️ This endpoint returns **200 even if check-in fails** — with `ok: false`.

    Reason: Gate staff need a clear "go" or "already used" signal. Returning
    error statuses would force the frontend to parse error bodies unnecessarily.

    Only system-level errors (permissions, malformed data) return non-200.
    """
    token = payload.token.strip()

    booking = db.scalar(select(Booking).where(Booking.qr_token == token))

    if booking is None:
        # ⚠️ Do not disclose why the scan failed to prevent token brute-forcing.
        logger.warning("Check-in: unknown token scanned by user %s", staff.id)
        return _result(ok=False, reason="invalid_ticket")

    seat = db.get(Seat, booking.seat_id)
    event = db.get(Event, booking.event_id)
    attendee = db.get(User, booking.user_id)

    # ---- Authorization: restrict scanning to owned events ----
    #
    # Prevents organizers from scanning tickets for other events.
    # Follows the role-vs-ownership pattern from Phase 10.
    if staff.role != ROLE_ADMIN and event.organizer_id != staff.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "This ticket does not belong to your event."
        )

    if booking.status != BOOKING_CONFIRMED:
        return _result(
            ok=False, reason="booking_cancelled",
            booking=booking, seat=seat, event=event, attendee=attendee,
        )

    if booking.ticket_status != TICKET_READY:
        # Ticket not yet issued; suspicious activity.
        return _result(
            ok=False, reason="ticket_not_issued",
            booking=booking, seat=seat, event=event, attendee=attendee,
        )

    # ---- ⭐ ATOMIC CHECK-IN ----
    #
    # `WHERE checked_in_at IS NULL` acts as the primary guard.
    #
    # Concurrent scans:
    #   Both see NULL, both attempt UPDATE.
    #   First succeeds (rowcount 1).
    #   Second fails the WHERE clause (rowcount 0) -> "already used".
    #
    # This prevents race conditions by combining read and write into one step.
    result = db.execute(
        update(Booking)
        .where(Booking.id == booking.id, Booking.checked_in_at.is_(None))
        .values(checked_in_at=utcnow(), checked_in_by=staff.id)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(booking)

    if result.rowcount == 0:
        # Already checked in. Provide details for gate staff verification.
        scanner = db.get(User, booking.checked_in_by) if booking.checked_in_by else None
        logger.info("Duplicate check-in attempt: booking %s", booking.id)
        return _result(
            ok=False, reason="already_checked_in",
            booking=booking, seat=seat, event=event, attendee=attendee,
            scanned_by=scanner,
        )

    logger.info("✅ Check-in: booking %s by staff %s", booking.id, staff.id)
    return _result(
        ok=True, reason="checked_in",
        booking=booking, seat=seat, event=event, attendee=attendee,
        scanned_by=staff,
    )


@router.get("/events/{event_id}/stats")
def checkin_stats(
    event_id: int,
    db: Session = Depends(get_db),
    staff: User = Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN)),
):
    """Live gate counter for check-in statistics."""
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found.")

    if staff.role != ROLE_ADMIN and event.organizer_id != staff.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found.")

    from sqlalchemy import func

    confirmed = db.scalar(
        select(func.count(Booking.id)).where(
            Booking.event_id == event_id, Booking.status == BOOKING_CONFIRMED
        )
    )
    checked_in = db.scalar(
        select(func.count(Booking.id)).where(
            Booking.event_id == event_id,
            Booking.status == BOOKING_CONFIRMED,
            Booking.checked_in_at.is_not(None),
        )
    )

    return {
        "event_id": event_id,
        "event_name": event.name,
        "tickets_sold": confirmed,
        "checked_in": checked_in,
        "remaining": confirmed - checked_in,
    }
