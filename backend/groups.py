"""
Group booking ka core — jahan "sab ya koi nahi" ka faisla hota hai.

Routes se alag file isliye ki yahi logic teen jagah se chalta hai:
  - HTTP route (aakhri banda pay karta hai)
  - payment webhook (gateway confirm karta hai)
  - background job (deadline nikal jati hai)

---- Asli problem ----

Ek seat ki booking me "exactly once" ka matlab saaf hai: ek seat, ek
booking. Group me wo sawaal badal jata hai:

    4 log, 4 alag payments. 3 ka paisa aa chuka hai. Deadline aa gayi.
    Ab kya?

Jawab: **sab ya koi nahi.** Teen logon ko seat dena aur chauthe ko nahi,
poore group ka maqsad hi khatam kar deta hai (wo saath baithne aaye the).
To group tootta hai, seats chhootti hain, aur teeno ka paisa wapas jata hai.

---- Sabse mushkil race ----

    Thread A: aakhri banda pay kar raha hai       -> group confirm karna hai
    Thread B: expiry job chal raha hai            -> group todna hai

Dono theek us ek second me. Exactly EK ko jeetna chahiye, aur haarne wale
ko haar maan ke sahi cleanup karna chahiye.

Hal wahi hai jo poore project me hai — ek atomic conditional UPDATE:

    UPDATE group_bookings SET status = ? WHERE id = ? AND status = 'collecting'

rowcount 1 = maine faisla kiya. rowcount 0 = kisi aur ne pehle kar diya.
Koi lock nahi, koi wait nahi.
"""

import logging
import secrets
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from events_broadcast import broadcast_seat_update
from job_queue import enqueue_ticket
from models import (
    BOOKING_CONFIRMED,
    GROUP_CANCELLED,
    GROUP_COLLECTING,
    GROUP_CONFIRMED,
    GROUP_EXPIRED,
    PAYMENT_REFUNDED,
    SEAT_AVAILABLE,
    SEAT_BOOKED,
    SEAT_GROUP_HELD,
    SEAT_LOCKED,
    SHARE_PAID,
    SHARE_REFUNDED,
    SHARE_UNPAID,
    Booking,
    GroupBooking,
    GroupShare,
    Payment,
    Seat,
    utcnow,
)

logger = logging.getLogger(__name__)

# Group hold kitni der. Single seat hold 5 min ka hai; group me logon ko
# link bhejna, unhe kholna aur pay karna hota hai — 5 minute bahut kam hai.
DEFAULT_DEADLINE_MINUTES = 30
MAX_DEADLINE_MINUTES = 120
MAX_GROUP_SEATS = 10


def new_share_token() -> str:
    """
    Link ka secret.

    `secrets` module, `random` nahi — `random` predictable hai aur ek token
    guess kar lena matlab kisi aur ke group me ghus jaana. Wahi wajah jo
    Phase 12 me ticket QR token ke liye thi.
    """
    return secrets.token_urlsafe(24)


# ---------------------------------------------------------------------------
# Banana
# ---------------------------------------------------------------------------

class GroupError(Exception):
    """Business rule toota — routes ise 409/400 me badalte hain."""


def create_group(
    db: Session, *, user, seat_ids: list[int], deadline_minutes: int, quoted: dict[int, float]
) -> GroupBooking:
    """
    N seats ek saath hold karo aur group banao.

    ⚠️ Ye poora ek transaction hai, aur ye jaan-boojh ke hai.

    Ek bhi seat na mile to POORA group nahi banna chahiye — warna user ko
    3 seats mil jaati aur wo 4th ka intezaar karta rehta, jabki 4th kabhi
    milegi hi nahi. Aadhi hold kisi ke kaam ki nahi.
    """
    if not seat_ids:
        raise GroupError("Kam se kam ek seat chuno")
    if len(seat_ids) > MAX_GROUP_SEATS:
        raise GroupError(f"Ek group me max {MAX_GROUP_SEATS} seats")
    if len(set(seat_ids)) != len(seat_ids):
        raise GroupError("Ek hi seat do baar bheji gayi hai")

    minutes = max(5, min(deadline_minutes, MAX_DEADLINE_MINUTES))

    group = GroupBooking(
        event_id=db.get(Seat, seat_ids[0]).event_id,
        created_by=user.id,
        status=GROUP_COLLECTING,
        share_token=new_share_token(),
        expires_at=utcnow() + timedelta(minutes=minutes),
    )
    db.add(group)
    db.flush()

    for seat_id in seat_ids:
        # Har seat par wahi atomic claim jo single booking me hai.
        # `locked` bhi allow hai kyunki user ne aksar seats grid me select
        # (hold) ki hoti hain, phir group banata hai.
        result = db.execute(
            update(Seat)
            .where(
                Seat.id == seat_id,
                Seat.event_id == group.event_id,
                Seat.status.in_((SEAT_AVAILABLE, SEAT_LOCKED)),
            )
            .values(
                status=SEAT_GROUP_HELD,
                version=Seat.version + 1,
                locked_by=None,
                locked_until=None,
                held_price=None,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            # Rollback poore group ko wapas le jata hai — jo seats abhi
            # abhi hold ki thi wo bhi chhoot jaati hain. Yahi chahiye.
            db.rollback()
            raise GroupError(f"Seat {seat_id} ab available nahi hai")

        db.add(
            GroupShare(
                group_id=group.id,
                seat_id=seat_id,
                amount=quoted[seat_id],
                status=SHARE_UNPAID,
                # Banane wala pehli seat khud le leta hai — wo to aayega hi
                claimed_by=user.id if seat_id == seat_ids[0] else None,
            )
        )

    db.commit()

    for seat_id in seat_ids:
        broadcast_seat_update(db, seat_id, "group_held")

    db.refresh(group)
    return group


# ---------------------------------------------------------------------------
# Share claim karna
# ---------------------------------------------------------------------------

def claim_share(db: Session, share: GroupShare, user) -> GroupShare:
    """
    Ek khaali share apne naam karo.

    Do log ek hi share par ek saath click karein to ek hi ko milna chahiye.
    Wahi atomic conditional UPDATE — `WHERE claimed_by IS NULL`.
    """
    result = db.execute(
        update(GroupShare)
        .where(GroupShare.id == share.id, GroupShare.claimed_by.is_(None))
        .values(claimed_by=user.id)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        db.rollback()
        raise GroupError("Ye seat kisi aur ne le li")

    db.commit()
    db.refresh(share)
    return share


# ---------------------------------------------------------------------------
# ⭐ Paisa aana aur faisla
# ---------------------------------------------------------------------------

def mark_share_paid(db: Session, payment: Payment) -> None:
    """
    Ek share ka paisa aa gaya.

    Payment webhook se aata hai, isliye IDEMPOTENT hona zaroori hai —
    gateway wahi event do baar bhej sakta hai.
    """
    share = db.get(GroupShare, payment.group_share_id)
    if share is None:
        logger.error("Payment %s ka group share hi nahi mila", payment.id)
        return

    group = db.get(GroupBooking, share.group_id)

    # ⭐ Group pehle hi toot chuka hai (deadline nikal gayi) aur paisa ab
    # aaya. Ye race asli hai, sirf theory nahi.
    #
    # Is bande ko seat NAHI mil sakti — uski seat chhoot chuki hai aur
    # shayad kisi aur ne le bhi li hogi. Isliye seedha refund.
    if group.status != GROUP_COLLECTING:
        logger.warning(
            "Share %s ka paisa aaya par group %s ab '%s' hai — refund",
            share.id, group.id, group.status,
        )
        _refund_share(db, share, payment)
        db.commit()
        return

    if share.status == SHARE_UNPAID:
        share.status = SHARE_PAID
        share.payment_id = payment.id
    db.commit()

    _try_confirm(db, group)


def _try_confirm(db: Session, group: GroupBooking) -> bool:
    """
    Sab paid ho gaye? Tab group confirm karo aur bookings banao.

    Return: True agar isi call ne confirm kiya.
    """
    unpaid = db.scalar(
        select(GroupShare.id)
        .where(GroupShare.group_id == group.id, GroupShare.status == SHARE_UNPAID)
        .limit(1)
    )
    if unpaid is not None:
        return False        # abhi kuch log baaki hain

    # ⭐ YAHAN faisla hota hai.
    #
    # Do payments aakhri ho sakti hain (dono ne ek saath paid dekha), ya
    # expiry job bhi isi waqt group todh raha ho. Ye ek UPDATE tay karta
    # hai ki asal me kaun jeeta.
    result = db.execute(
        update(GroupBooking)
        .where(GroupBooking.id == group.id, GroupBooking.status == GROUP_COLLECTING)
        .values(status=GROUP_CONFIRMED, settled_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        db.rollback()
        return False        # koi aur pehle kar chuka

    # Yahan tak sirf EK caller pahunchta hai — ab bookings banana safe hai
    shares = db.scalars(
        select(GroupShare).where(GroupShare.group_id == group.id)
    ).all()

    bookings = []
    for share in shares:
        booking = Booking(
            user_id=share.claimed_by,
            seat_id=share.seat_id,
            event_id=group.event_id,
            status=BOOKING_CONFIRMED,
            amount=share.amount,
        )
        db.add(booking)
        db.flush()
        share.booking_id = booking.id
        bookings.append(booking)

        db.execute(
            update(Seat)
            .where(Seat.id == share.seat_id)
            .values(status=SEAT_BOOKED, version=Seat.version + 1)
            .execution_options(synchronize_session=False)
        )
        if share.payment_id:
            payment = db.get(Payment, share.payment_id)
            payment.booking_id = booking.id

    db.commit()

    # Commit ke BAAD — tickets aur broadcast. Pehle karte to fail hone par
    # tickets ban chuke hote bina bookings ke.
    for share in shares:
        broadcast_seat_update(db, share.seat_id, "booked")
    for booking in bookings:
        enqueue_ticket(booking.id)

    logger.info("Group %s confirmed — %s bookings", group.id, len(bookings))
    return True


# ---------------------------------------------------------------------------
# Todna — deadline ya cancel
# ---------------------------------------------------------------------------

def break_group(db: Session, group: GroupBooking, reason: str) -> bool:
    """
    Group todo — seats chhodo, jo paise aaye the wo wapas.

    `reason` = GROUP_EXPIRED ya GROUP_CANCELLED.

    Return: True agar isi call ne toda. False matlab koi aur pehle kar
    chuka tha (confirm ho gaya ho, ya doosra job).
    """
    result = db.execute(
        update(GroupBooking)
        .where(GroupBooking.id == group.id, GroupBooking.status == GROUP_COLLECTING)
        .values(status=reason, settled_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        db.rollback()
        return False

    shares = db.scalars(
        select(GroupShare).where(GroupShare.group_id == group.id)
    ).all()

    for share in shares:
        if share.status == SHARE_PAID and share.payment_id:
            _refund_share(db, share, db.get(Payment, share.payment_id))

        # Seat sirf tab chhodo jab wo abhi bhi IS group ke hold me ho.
        # WHERE me status check zaroori hai — bina iske ek purani job
        # kisi aur ki booked seat ko available kar sakti thi.
        db.execute(
            update(Seat)
            .where(Seat.id == share.seat_id, Seat.status == SEAT_GROUP_HELD)
            .values(
                status=SEAT_AVAILABLE,
                version=Seat.version + 1,
                locked_by=None,
                locked_until=None,
                held_price=None,
            )
            .execution_options(synchronize_session=False)
        )

    db.commit()

    for share in shares:
        broadcast_seat_update(db, share.seat_id, "released")

    logger.info("Group %s %s — %s seats chhodi", group.id, reason, len(shares))
    return True


def _refund_share(db: Session, share: GroupShare, payment: Payment | None) -> None:
    """
    Ek share ka paisa wapas.

    ⚠️ Mock provider me ye sirf status likhta hai. Asli gateway me yahan
    refund API call hoti aur uska confirmation bhi WEBHOOK se aata — bilkul
    waise hi jaise payment ka aata hai. Yaani `refund_pending` naam ka ek
    aur state chahiye hota.

    Maine wo abhi nahi banaya, aur ise "ho gaya" bhi nahi keh raha — ye
    jaan-boojh ke chhoda gaya hissa hai.
    """
    share.status = SHARE_REFUNDED
    if payment is not None:
        payment.status = PAYMENT_REFUNDED
        payment.failure_reason = "group_broken"


def expire_due_groups(db: Session) -> int:
    """
    Jinki deadline nikal gayi un sab groups ko todo.

    Ye background job se chalta hai, kisi request se nahi.

    Wajah: seat hold expire karna aur REFUND karna do alag kaam hain.
    Baaki jagah hum "lazy cleanup" karte hain (koi seats padhe to expired
    locks saaf ho jaate hain) — par wo yahan nahi chalega. Agar kisi ne
    is event ka page hi na khola, to lazy cleanup kabhi chalta hi nahi,
    aur log apne paise ka intezaar karte rehte.

    Paisa wapas karna kisi ke page kholne par nirbhar nahi ho sakta.
    """
    due = db.scalars(
        select(GroupBooking).where(
            GroupBooking.status == GROUP_COLLECTING,
            GroupBooking.expires_at < utcnow(),
        )
    ).all()

    broken = 0
    for group in due:
        try:
            if break_group(db, group, GROUP_EXPIRED):
                broken += 1
        except Exception:
            # Ek group fail ho to baaki mat rok — har group apne me alag hai
            db.rollback()
            logger.exception("Group %s expire karte waqt fail", group.id)

    return broken
