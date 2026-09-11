"""
Payment routes.

⭐ The core challenge in this phase is not gateway integration — that is trivial
with documentation. The real challenge is the "Dual-Write" problem inherent to
payments:

    The user was charged, but the booking failed. Now what?

We must keep two systems (the gateway and our database) consistent, even though
either can fail at any time.

---- Design Decisions ----

1. WEBHOOKS ARE THE SOURCE OF TRUTH, not browser redirects.
   Redirects are unreliable:
     - Users may close the tab after payment -> redirect never occurs, but the
       charge succeeded. The booking must still be created.
     - Malicious users may hit the success URL directly -> this would create
       bookings without payment.
   Redirects are strictly for UI/UX ("thank you" pages), not for business logic.

2. FULFILMENT IS IDEMPOTENT.
   Webhooks are "at-least-once" delivery — the gateway may send the same event
   multiple times if a response is missed. Fulfilment logic must handle
   duplicate calls gracefully without creating duplicate bookings.

3. SEATS REMAIN payment_pending DURING PAYMENT.
   available -> locked -> payment_pending -> booked
                              |
                     (fail/timeout) -> available
"""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import get_current_user
from config import settings
from database import get_db
from groups import mark_share_paid
from events_broadcast import broadcast_seat_update
from pricing_state import price_now
from models import (
    BOOKING_CONFIRMED,
    PAYMENT_EXPIRED,
    PAYMENT_FAILED,
    PAYMENT_PENDING,
    PAYMENT_SUCCEEDED,
    SEAT_AVAILABLE,
    SEAT_BOOKED,
    SEAT_LOCKED,
    SEAT_PAYMENT_PENDING,
    Booking,
    Payment,
    Seat,
    User,
    utcnow,
)
from job_queue import enqueue_ticket
from payments import PaymentError, get_provider
from rate_limit import BOOKING, limit_user
from redis_client import acquire_seat_lock, get_lock_owner, redis_client, release_seat_lock
from schemas import CheckoutOut, CheckoutRequest, PaymentOut, SimulateRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payments", tags=["payments"])


def _to_out(payment: Payment) -> PaymentOut:
    return PaymentOut(
        id=payment.id,
        seat_id=payment.seat_id,
        event_id=payment.event_id,
        booking_id=payment.booking_id,
        status=payment.status,
        amount=float(payment.amount),
        currency=payment.currency,
        provider=payment.provider,
        failure_reason=payment.failure_reason,
        expires_at=payment.expires_at,
        created_at=payment.created_at,
    )


# ---------------------------------------------------------------------------
# Initialize checkout
# ---------------------------------------------------------------------------

@router.post(
    "/checkout",
    response_model=CheckoutOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limit_user(BOOKING))],
)
def start_checkout(
    payload: CheckoutRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Create a payment session for a seat.

    Requires a Redis lock to prevent concurrent payment attempts for the same seat.
    """
    seat = db.get(Seat, payload.seat_id)
    if seat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Seat not found")

    if seat.status == SEAT_BOOKED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Seat is already booked")

    # ---- Lock verification ----
    owner = get_lock_owner(payload.seat_id)
    if owner is None:
        # Lock expired; attempt to re-acquire.
        if not acquire_seat_lock(payload.seat_id, user.id, ttl=settings.PAYMENT_TTL_SECONDS):
            raise HTTPException(status.HTTP_409_CONFLICT, "Seat is held by another user")
    elif owner != user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Seat is held by another user")
    else:
        # ⚠️ Extend lock TTL. Checkout requires user input; prevent expiration
        # during the payment process.
        redis_client.expire(f"seat:{payload.seat_id}:lock", settings.PAYMENT_TTL_SECONDS)

    # Check for existing pending payments to maintain idempotency.
    existing = db.scalar(
        select(Payment).where(
            Payment.seat_id == payload.seat_id,
            Payment.status == PAYMENT_PENDING,
        )
    )
    if existing:
        if existing.user_id != user.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Payment already in progress for this seat")
        if existing.expires_at > utcnow():
            return CheckoutOut(
                payment_id=existing.id,
                checkout_url=_checkout_url_for(existing),
                provider=existing.provider,
                amount=float(existing.amount),
                expires_at=existing.expires_at,
            )
        # Expired; mark as such and proceed to create a new one.
        existing.status = PAYMENT_EXPIRED
        db.flush()

    provider = get_provider()
    expires_at = utcnow() + timedelta(seconds=settings.PAYMENT_TTL_SECONDS)

    # ⭐ Calculate price once to ensure consistency between DB and gateway.
    quoted = price_now(db, seat)

    payment = Payment(
        user_id=user.id,
        seat_id=seat.id,
        event_id=seat.event_id,
        amount=quoted,
        currency=settings.CURRENCY,
        provider=provider.name,
        status=PAYMENT_PENDING,
        expires_at=expires_at,
    )
    db.add(payment)

    try:
        # ⚠️ Flush to generate payment ID for the gateway.
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Payment already in progress for this seat")

    try:
        session = provider.create_checkout(
            payment_id=payment.id,
            amount=quoted,
            description=f"Seat {seat.row_label}-{seat.seat_number}",
        )
    except PaymentError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))

    payment.provider_ref = session.reference

    # Update seat status to payment_pending.
    db.execute(
        update(Seat)
        .where(Seat.id == seat.id, Seat.status.in_((SEAT_AVAILABLE, SEAT_LOCKED)))
        .values(
            status=SEAT_PAYMENT_PENDING,
            locked_by=user.id,
            locked_until=expires_at,
            version=Seat.version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()

    broadcast_seat_update(db, seat.id, "payment_pending")

    return CheckoutOut(
        payment_id=payment.id,
        checkout_url=session.url,
        provider=provider.name,
        amount=float(payment.amount),
        expires_at=expires_at,
    )


def _checkout_url_for(payment: Payment) -> str:
    frontend = settings.FRONTEND_URL.rstrip("/")
    if payment.provider == "mock":
        return f"{frontend}/pay/{payment.id}"
    return f"{frontend}/payment/return?payment_id={payment.id}"


# ---------------------------------------------------------------------------
# ⭐ Fulfilment — entry point for both webhooks and reconciliation
# ---------------------------------------------------------------------------

def _fulfil(db: Session, payment: Payment) -> Booking | None:
    """
    Finalize payment: create booking and update seat status.

    ⚠️ Group share payments are handled separately in groups.py.
    """
    # If already succeeded, return the existing booking.
    if payment.status == PAYMENT_SUCCEEDED and payment.booking_id:
        return db.get(Booking, payment.booking_id)

    if payment.group_share_id is not None:
        payment.status = PAYMENT_SUCCEEDED
        db.commit()
        mark_share_paid(db, payment)
        return None

    seat = db.get(Seat, payment.seat_id)

    # Optimistic update: transition seat to booked.
    result = db.execute(
        update(Seat)
        .where(
            Seat.id == payment.seat_id,
            Seat.status.in_((SEAT_PAYMENT_PENDING, SEAT_LOCKED, SEAT_AVAILABLE)),
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
        # Seat was taken; mark payment as failed to trigger refund flow.
        db.rollback()
        payment.status = PAYMENT_FAILED
        payment.failure_reason = "seat_taken_after_payment"
        db.commit()
        logger.error("Payment %s succeeded but seat %s was taken — REFUND REQUIRED",
                     payment.id, payment.seat_id)
        raise HTTPException(status.HTTP_409_CONFLICT, "Seat unavailable; refund process initiated")

    booking = Booking(
        user_id=payment.user_id,
        seat_id=payment.seat_id,
        event_id=payment.event_id,
        status=BOOKING_CONFIRMED,
        amount=payment.amount,
    )
    db.add(booking)

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(Booking).where(
                Booking.seat_id == payment.seat_id,
                Booking.status == BOOKING_CONFIRMED,
            )
        )
        payment.status = PAYMENT_SUCCEEDED
        payment.booking_id = existing.id if existing else None
        db.commit()
        return existing

    payment.status = PAYMENT_SUCCEEDED
    payment.booking_id = booking.id
    db.commit()

    release_seat_lock(payment.seat_id, payment.user_id)
    broadcast_seat_update(db, payment.seat_id, "booked")

    enqueue_ticket(booking.id)

    db.refresh(booking)
    return booking


def _fail(db: Session, payment: Payment, reason: str) -> None:
    """Handle payment failure: release the seat."""
    if payment.status != PAYMENT_PENDING:
        return

    payment.status = PAYMENT_FAILED
    payment.failure_reason = reason

    db.execute(
        update(Seat)
        .where(Seat.id == payment.seat_id, Seat.status == SEAT_PAYMENT_PENDING)
        .values(
            status=SEAT_AVAILABLE,
            locked_by=None,
            locked_until=None,
            version=Seat.version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()

    release_seat_lock(payment.seat_id, payment.user_id)
    broadcast_seat_update(db, payment.seat_id, "payment_failed")


# ---------------------------------------------------------------------------
# Webhook — Stripe integration
# ---------------------------------------------------------------------------

@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """
    ⭐ Stripe webhook: The primary source of truth.
    """
    raw = await request.body()
    provider = get_provider()

    try:
        event = provider.verify_webhook(raw, request.headers.get("stripe-signature"))
    except PaymentError as exc:
        logger.warning("Webhook rejected: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    event_type = event.get("type", "")
    obj = event.get("data", {}).get("object", {})
    session_id = obj.get("id")

    payment = db.scalar(select(Payment).where(Payment.provider_ref == session_id))
    if payment is None:
        logger.warning("Webhook for unknown session %s", session_id)
        return {"received": True, "handled": False}

    if event_type == "checkout.session.completed":
        _fulfil(db, payment)
    elif event_type in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        _fail(db, payment, event_type)

    return {"received": True, "handled": True}


# ---------------------------------------------------------------------------
# Mock checkout — for development/testing
# ---------------------------------------------------------------------------

@router.post("/{payment_id}/simulate", response_model=PaymentOut)
def simulate_payment(
    payment_id: int,
    payload: SimulateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Mock gateway simulator.
    """
    if settings.payment_provider != "mock":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Simulation only available for mock provider",
        )

    payment = db.get(Payment, payment_id)
    if payment is None or payment.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")

    if payment.status != PAYMENT_PENDING:
        return _to_out(payment)

    if payment.expires_at < utcnow():
        _fail(db, payment, "expired")
        db.refresh(payment)
        return _to_out(payment)

    if payload.outcome == "success":
        _fulfil(db, payment)
    else:
        _fail(db, payment, "declined_by_user")

    db.refresh(payment)
    return _to_out(payment)


# ---------------------------------------------------------------------------
# Status — polling endpoint for the frontend
# ---------------------------------------------------------------------------

@router.get("/{payment_id}", response_model=PaymentOut)
def get_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    payment = db.get(Payment, payment_id)
    if payment is None or payment.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")

    # Lazy cleanup for expired payments.
    if payment.status == PAYMENT_PENDING and payment.expires_at < utcnow():
        _fail(db, payment, "expired")
        db.refresh(payment)

    return _to_out(payment)


@router.get("", response_model=list[PaymentOut])
def my_payments(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    payments = db.scalars(
        select(Payment).where(Payment.user_id == user.id).order_by(Payment.created_at.desc())
    ).all()
    return [_to_out(p) for p in payments]
