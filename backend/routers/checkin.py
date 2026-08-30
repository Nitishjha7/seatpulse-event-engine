"""
Gate check-in — QR scan karke entry validate karo.

⭐ Ye wahi "exactly once" problem hai jo seat booking me thi, sirf alag
kapdon me:

    Seat booking  : ek seat, ek confirmed booking
    Check-in      : ek ticket, ek entry

Aur hal bhi wahi hai — ek atomic conditional UPDATE. Do gates ek saath
wahi QR scan karein to sirf ek ko `rowcount 1` milega.

Kyu ye matter karta hai: agar do log ek hi QR ka screenshot leke alag
gates pe chale jaayein, to dono andar nahi jaane chahiye. Ye ticketing
fraud ka sabse aam tarika hai.
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
    # Gate pe ek staff member tez tez scan karta hai — burst allow hona
    # chahiye, par ek script hazaaron tokens brute-force na kar sake.
    dependencies=[Depends(limit_user(SEAT_LOCK))],
)
def check_in(
    payload: CheckInRequest,
    db: Session = Depends(get_db),
    staff: User = Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN)),
):
    """
    QR token scan karo aur entry mark karo.

    ⚠️ Ye endpoint **200 lautata hai chahe check-in fail ho** — `ok: false`
    ke saath.

    Kyu: gate pe khada banda scanner me HTTP status nahi dekhta. Use ek
    saaf jawab chahiye — "andar jao" ya "ye ticket already use ho chuka
    hai, 7:42 pm par". Error status dene se frontend ko error handling me
    wahi jaankari dobara nikalni padti.

    Sirf asli errors (permission, malformed) hi non-200 hain.
    """
    token = payload.token.strip()

    booking = db.scalar(select(Booking).where(Booking.qr_token == token))

    if booking is None:
        # ⚠️ Kaunsa hissa galat tha, ye NAHI batate — warna koi tokens
        # brute-force karke valid ones dhoondh sakta hai.
        logger.warning("Check-in: unknown token scanned by user %s", staff.id)
        return _result(ok=False, reason="invalid_ticket")

    seat = db.get(Seat, booking.seat_id)
    event = db.get(Event, booking.event_id)
    attendee = db.get(User, booking.user_id)

    # ---- Authorization: sirf apne event ke tickets scan kar sakte ho ----
    #
    # Bina iske ek organizer kisi bhi event ke tickets check-in kar deta.
    # Wahi role-vs-ownership wala farak jo Phase 10 me tha.
    if staff.role != ROLE_ADMIN and event.organizer_id != staff.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Ye ticket tumhare event ka nahi hai"
        )

    if booking.status != BOOKING_CONFIRMED:
        return _result(
            ok=False, reason="booking_cancelled",
            booking=booking, seat=seat, event=event, attendee=attendee,
        )

    if booking.ticket_status != TICKET_READY:
        # Ticket abhi bana hi nahi — QR kahan se aaya? Shak wali baat hai.
        return _result(
            ok=False, reason="ticket_not_issued",
            booking=booking, seat=seat, event=event, attendee=attendee,
        )

    # ---- ⭐ ATOMIC CHECK-IN ----
    #
    # `WHERE checked_in_at IS NULL` — yahi poora guard hai.
    #
    # Do gates ek saath wahi QR scan karein:
    #   dono ko booking milti hai, dono ko checked_in_at NULL dikhta hai
    #   dono UPDATE chalate hain
    #   pehla jeetta hai -> rowcount 1
    #   dusre ka WHERE ab match nahi karta -> rowcount 0 -> "already used"
    #
    # Bilkul wahi pattern jo seat booking me hai. Read aur write alag
    # steps nahi hain, isliye beech me kuch ghus nahi sakta.
    result = db.execute(
        update(Booking)
        .where(Booking.id == booking.id, Booking.checked_in_at.is_(None))
        .values(checked_in_at=utcnow(), checked_in_by=staff.id)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(booking)

    if result.rowcount == 0:
        # Pehle se andar aa chuka hai. Kab aur kisne — dono batate hain,
        # kyunki gate pe wahi sawaal poocha jata hai.
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
    """Gate pe live counter — kitne andar aa chuke hain."""
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event nahi mila")

    if staff.role != ROLE_ADMIN and event.organizer_id != staff.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event nahi mila")

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
