"""
Payment routes.

⭐ Is phase ki asli problem gateway integrate karna nahi hai — wo docs padh
ke koi bhi kar leta hai. Asli problem wo hai jo payments MAJBOORI me laate
hain:

    Paisa kat gaya, par booking fail ho gayi. Ab kya?

Ye classic DUAL-WRITE problem hai: do systems (gateway aur hamara database)
ko consistent rakhna, jab dono me se koi bhi kabhi bhi fail ho sakta hai.

---- Design ke teen faisle ----

1. WEBHOOK SOURCE OF TRUTH HAI, browser redirect nahi.
   Redirect par bharosa nahi kar sakte:
     - user pay karke tab band kar de -> redirect aata hi nahi, par paisa
       kat chuka hai. Booking honi CHAHIYE.
     - koi seedha success URL hit kar de -> bina paise ke booking ban jayegi.
   Redirect sirf "thank you" page dikhane ke liye hai, faisla lene ke liye nahi.

2. FULFILMENT IDEMPOTENT HAI.
   Webhooks AT-LEAST-ONCE hote hain — gateway same event do baar bhej sakta
   hai agar pehla response miss ho jaye. To fulfil dobara chale to naya kaam
   na ho, wahi booking wapas mile.

3. SEAT PAYMENT KE DAURAAN payment_pending REHTI HAI.
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
from events_broadcast import broadcast_seat_update
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
# Checkout shuru karo
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
    Seat ke liye payment session banao.

    User ke paas is seat ka Redis lock hona chahiye — matlab usne pehle
    seat select ki hui hai. Bina lock ke checkout allow karte to do log
    ek hi seat ka payment shuru kar dete, aur ek ka paisa refund karna padta.
    """
    seat = db.get(Seat, payload.seat_id)
    if seat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Seat nahi mili")

    if seat.status == SEAT_BOOKED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Seat pehle se booked hai")

    # ---- Lock verify ----
    owner = get_lock_owner(payload.seat_id)
    if owner is None:
        # Lock TTL pe chhut gaya — dobara lene ki koshish karo
        if not acquire_seat_lock(payload.seat_id, user.id, ttl=settings.PAYMENT_TTL_SECONDS):
            raise HTTPException(status.HTTP_409_CONFLICT, "Seat abhi kisi aur ne hold kar li")
    elif owner != user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ye seat kisi aur ke paas hold hai")
    else:
        # ⚠️ Lock ki TTL badha do. Default hold 5 min ka hai par checkout me
        # user ko card details bharni hoti hain — beech me lock chhut jaye to
        # paisa kat jayega aur seat kisi aur ki ho chuki hogi.
        redis_client.expire(f"seat:{payload.seat_id}:lock", settings.PAYMENT_TTL_SECONDS)

    # Purana pending payment hai? Wahi session wapas do — naya mat banao.
    # (Ye bhi idempotency ka ek roop hai: user ne back dabaya aur phir se
    # "Pay" click kiya, to do sessions nahi banne chahiye.)
    existing = db.scalar(
        select(Payment).where(
            Payment.seat_id == payload.seat_id,
            Payment.status == PAYMENT_PENDING,
        )
    )
    if existing:
        if existing.user_id != user.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Is seat ka payment already chal raha hai")
        if existing.expires_at > utcnow():
            return CheckoutOut(
                payment_id=existing.id,
                checkout_url=_checkout_url_for(existing),
                provider=existing.provider,
                amount=float(existing.amount),
                expires_at=existing.expires_at,
            )
        # Expire ho chuka — usse band karke naya banate hain
        existing.status = PAYMENT_EXPIRED
        db.flush()

    provider = get_provider()
    expires_at = utcnow() + timedelta(seconds=settings.PAYMENT_TTL_SECONDS)

    payment = Payment(
        user_id=user.id,
        seat_id=seat.id,
        event_id=seat.event_id,
        amount=float(seat.price),
        currency=settings.CURRENCY,
        provider=provider.name,
        status=PAYMENT_PENDING,
        expires_at=expires_at,
    )
    db.add(payment)

    try:
        # ⚠️ Yahan flush karte hain, commit nahi — payment id chahiye gateway
        # ko bhejne ke liye, par transaction abhi khuli rakhni hai.
        db.flush()
    except IntegrityError:
        # Partial unique index ne pakda: is seat ka pending payment already hai.
        # Do parallel checkout requests me se ek yahin ruk jayega.
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Is seat ka payment already chal raha hai")

    try:
        session = provider.create_checkout(
            payment_id=payment.id,
            amount=float(seat.price),
            description=f"Seat {seat.row_label}-{seat.seat_number}",
        )
    except PaymentError as exc:
        # Gateway se baat nahi hui — payment row mat chhodo, warna seat ka
        # pending payment atka rahega aur user retry nahi kar payega.
        db.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))

    payment.provider_ref = session.reference

    # Seat ko payment_pending karo — dusre users ko grid me dikh jayega
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
    # Stripe session URL sirf ek baar milta hai. Dobara chahiye to session
    # retrieve karna padta — abhi return page pe bhej dete hain, jo status
    # dekh ke user ko bata dega.
    return f"{frontend}/payment/return?payment_id={payment.id}"


# ---------------------------------------------------------------------------
# ⭐ Fulfilment — dono raaste yahin milte hain
# ---------------------------------------------------------------------------

def _fulfil(db: Session, payment: Payment) -> Booking:
    """
    Payment succeed hua — booking banao aur seat book karo.

    ⚠️ IDEMPOTENT hona zaroori hai. Webhooks at-least-once hote hain, aur
    reconciliation job bhi isi ko call karta hai. Do baar chale to dusri
    baar wahi booking wapas milni chahiye, nayi nahi.
    """
    # Pehle se ho chuka? Wahi booking lauta do.
    if payment.status == PAYMENT_SUCCEEDED and payment.booking_id:
        return db.get(Booking, payment.booking_id)

    seat = db.get(Seat, payment.seat_id)

    # Optimistic update — wahi pattern jo direct booking me hai.
    # payment_pending se booked, ya locked se (agar reconciliation se aaye
    # aur beech me lock expire ho gaya ho).
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
        # Seat kisi aur ne le li — paisa kat chuka hai, refund karna padega.
        #
        # ⚠️ Ye theoretically nahi hona chahiye (lock hamare paas tha), par
        # "nahi hona chahiye" aur "nahi hoga" alag baatein hain. Isliye ise
        # chupchap ignore nahi kar rahe — payment ko failed mark karke reason
        # likh dete hain, taki refund flow ise utha sake.
        db.rollback()
        payment.status = PAYMENT_FAILED
        payment.failure_reason = "seat_taken_after_payment"
        db.commit()
        logger.error("Payment %s succeeded par seat %s le li gayi — REFUND CHAHIYE",
                     payment.id, payment.seat_id)
        raise HTTPException(status.HTTP_409_CONFLICT, "Seat le li gayi — refund process hoga")

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
        # Layer 3 — partial unique index. Booking pehle se hai.
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

    db.refresh(booking)
    return booking


def _fail(db: Session, payment: Payment, reason: str) -> None:
    """Payment fail — seat wapas available karo."""
    if payment.status != PAYMENT_PENDING:
        return      # already settled, kuch mat karo

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
# Webhook — Stripe yahan bolta hai
# ---------------------------------------------------------------------------

@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """
    ⭐ Stripe ka webhook. Yahi asli source of truth hai.

    ⚠️ Ye endpoint AUTHENTICATED nahi ho sakta — Stripe ke paas hamara JWT
    nahi hai. Iski jagah SIGNATURE hi authentication hai. Bina verify kiye
    koi bhi POST maar ke free ticket le leta.

    Aur raw body chahiye — parsed JSON nahi. Signature exact bytes par bani
    hai; JSON parse karke dobara serialize karoge to spacing badal jayegi
    aur signature match nahi karegi.
    """
    raw = await request.body()
    provider = get_provider()

    try:
        event = provider.verify_webhook(raw, request.headers.get("stripe-signature"))
    except PaymentError as exc:
        logger.warning("Webhook reject: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    event_type = event.get("type", "")
    obj = event.get("data", {}).get("object", {})
    session_id = obj.get("id")

    payment = db.scalar(select(Payment).where(Payment.provider_ref == session_id))
    if payment is None:
        # Unknown session — 200 hi lautao, warna Stripe hamesha retry karta
        # rahega. Log karke aage badh jao.
        logger.warning("Webhook for unknown session %s", session_id)
        return {"received": True, "handled": False}

    if event_type == "checkout.session.completed":
        _fulfil(db, payment)
    elif event_type in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        _fail(db, payment, event_type)

    # Stripe ko 200 chahiye. Non-2xx doge to wo retry karta rahega.
    return {"received": True, "handled": True}


# ---------------------------------------------------------------------------
# Mock checkout — jab Stripe keys na hon
# ---------------------------------------------------------------------------

@router.post("/{payment_id}/simulate", response_model=PaymentOut)
def simulate_payment(
    payment_id: int,
    payload: SimulateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Mock provider ka "gateway".

    Wahi `_fulfil` / `_fail` call karta hai jo asli webhook karta hai —
    matlab hum mock ke liye alag code path test nahi kar rahe. Sirf trigger
    alag hai, logic bilkul same.
    """
    if settings.payment_provider != "mock":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Simulate sirf mock provider ke saath chalta hai",
        )

    payment = db.get(Payment, payment_id)
    if payment is None or payment.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment nahi mila")

    if payment.status != PAYMENT_PENDING:
        return _to_out(payment)     # already settled — idempotent

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
# Status — frontend return page isse poll karta hai
# ---------------------------------------------------------------------------

@router.get("/{payment_id}", response_model=PaymentOut)
def get_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    payment = db.get(Payment, payment_id)
    # 404 (403 nahi) — dusre ko ye bhi na pata chale ki ye payment exist karta hai
    if payment is None or payment.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment nahi mila")

    # Expire ho gaya par kisi ne settle nahi kiya — abhi kar do.
    # Ye "lazy cleanup" hai, wahi pattern jo expired seat locks me hai.
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
