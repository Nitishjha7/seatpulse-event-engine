"""
Core group booking logic — handles the "all or nothing" decision.

Separated from routes because this logic is triggered by three sources:
  - HTTP route (final user payment)
  - Payment webhook (gateway confirmation)
  - Background job (deadline expiry)

---- The Problem ----

In single-seat bookings, "exactly once" is straightforward: one seat, one booking.
In groups, the requirement changes:

    4 people, 4 separate payments. 3 have paid. The deadline is reached.
    What now?

Answer: **All or nothing.** Giving seats to three people while denying the fourth
defeats the purpose of a group (they intended to sit together). Therefore, the
group is dissolved, seats are released, and the three payments are refunded.

---- The Race Condition ----

    Thread A: Final user paying      -> confirm group
    Thread B: Expiry job running    -> dissolve group

Both occur simultaneously. Exactly one must succeed, and the loser must perform
the correct cleanup.

The solution is an atomic conditional UPDATE:

    UPDATE group_bookings SET status = ? WHERE id = ? AND status = 'collecting'

rowcount 1 = I made the decision. rowcount 0 = Someone else beat me to it.
No locks, no waiting.
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
    PAYMENT_EXPIRED,
    PAYMENT_PENDING,
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

# Group hold duration. Single seat holds are 5 minutes; groups require
# sharing links and coordinating payments, so 5 minutes is insufficient.
DEFAULT_DEADLINE_MINUTES = 30
MAX_DEADLINE_MINUTES = 120
MAX_GROUP_SEATS = 10


def new_share_token() -> str:
    """
    Generates a secret for the share link.

    Uses `secrets` instead of `random` to prevent predictability, which would
    allow unauthorized access to other groups. Same rationale as the ticket
    QR token in Phase 12.
    """
    return secrets.token_urlsafe(24)


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------

class GroupError(Exception):
    """Business rule violation — routes map this to 409/400 responses."""


def create_group(
    db: Session, *, user, seat_ids: list[int], deadline_minutes: int, quoted: dict[int, float]
) -> GroupBooking:
    """
    Holds N seats and initializes a group.

    ⚠️ This is a single transaction by design.

    If any seat is unavailable, the entire group must fail. Otherwise, a user
    might hold 3 seats while waiting indefinitely for a 4th that will never
    become available. Partial holds are not useful.
    """
    if not seat_ids:
        raise GroupError("At least one seat must be selected.")
    if len(seat_ids) > MAX_GROUP_SEATS:
        raise GroupError(f"Maximum {MAX_GROUP_SEATS} seats allowed per group.")
    if len(set(seat_ids)) != len(seat_ids):
        raise GroupError("Duplicate seat selection detected.")

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
        # Atomic claim similar to single bookings. `locked` status is allowed
        # because users often select seats in the grid before forming a group.
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
            # Rollback releases all seats held in this transaction.
            db.rollback()
            raise GroupError(f"Seat {seat_id} is no longer available.")

        db.add(
            GroupShare(
                group_id=group.id,
                seat_id=seat_id,
                amount=quoted[seat_id],
                status=SHARE_UNPAID,
                claimed_by=user.id if seat_id == seat_ids[0] else None,
            )
        )

    db.commit()

    for seat_id in seat_ids:
        broadcast_seat_update(db, seat_id, "group_held")

    db.refresh(group)
    return group


# ---------------------------------------------------------------------------
# Claiming a share
# ---------------------------------------------------------------------------

def claim_share(db: Session, share: GroupShare, user) -> GroupShare:
    """
    Claims an empty share.

    Uses an atomic conditional UPDATE (`WHERE claimed_by IS NULL`) to ensure
    only one user can claim a specific share.
    """
    result = db.execute(
        update(GroupShare)
        .where(GroupShare.id == share.id, GroupShare.claimed_by.is_(None))
        .values(claimed_by=user.id)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        db.rollback()
        raise GroupError("This seat has already been claimed.")

    db.commit()
    db.refresh(share)
    return share


# ---------------------------------------------------------------------------
# ⭐ Payment and Decision
# ---------------------------------------------------------------------------

def mark_share_paid(db: Session, payment: Payment) -> None:
    """
    Marks a share as paid.

    Must be idempotent to handle duplicate webhook events from the gateway.
    """
    share = db.get(GroupShare, payment.group_share_id)
    if share is None:
        logger.error("Group share not found for payment %s", payment.id)
        return

    # ⭐⭐ Uses a PESSIMISTIC LOCK on the group row.
    #
    # While the project generally uses optimistic locking, this is an intentional
    # exception for correctness. Without this lock, a race condition exists:
    #
    #   payment thread          expiry job
    #   --------------          ----------
    #   read group status
    #     -> 'collecting'
    #                           set group to 'expired'
    #                           read shares -> share is 'unpaid'
    #                           -> no refund triggered
    #   set share to 'paid'
    #
    # Result: A 'paid' share in an 'expired' group. The user is charged, but
    # the seat is not booked and no refund is issued.
    #
    # Optimistic patterns fail here because two different rows (group and share)
    # are involved. `FOR UPDATE` serializes these operations.
    group = db.execute(
        select(GroupBooking).where(GroupBooking.id == share.group_id).with_for_update()
    ).scalar_one()

    # Now the read is reliable; the expiry job has either committed or is
    # waiting for our transaction to finish.
    if group.status != GROUP_COLLECTING:
        logger.warning(
            "Payment received for share %s, but group %s is now '%s' — refunding.",
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
    Checks if all shares are paid. If so, confirms the group and creates bookings.

    Returns: True if this call performed the confirmation.
    """
    unpaid = db.scalar(
        select(GroupShare.id)
        .where(GroupShare.group_id == group.id, GroupShare.status == SHARE_UNPAID)
        .limit(1)
    )
    if unpaid is not None:
        return False

    # ⭐ The decision point.
    #
    # Multiple payments might trigger this simultaneously, or an expiry job
    # might be running. This UPDATE determines the winner.
    result = db.execute(
        update(GroupBooking)
        .where(GroupBooking.id == group.id, GroupBooking.status == GROUP_COLLECTING)
        .values(status=GROUP_CONFIRMED, settled_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        db.rollback()
        return False

    # Only one caller reaches this point.
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

    # Broadcast and enqueue tickets AFTER commit to ensure data integrity.
    for share in shares:
        broadcast_seat_update(db, share.seat_id, "booked")
    for booking in bookings:
        enqueue_ticket(booking.id)

    logger.info("Group %s confirmed — %s bookings created.", group.id, len(bookings))
    return True


# ---------------------------------------------------------------------------
# Dissolution — deadline or cancellation
# ---------------------------------------------------------------------------

def break_group(db: Session, group: GroupBooking, reason: str) -> bool:
    """
    Dissolves a group, releases seats, and refunds payments.

    `reason` = GROUP_EXPIRED or GROUP_CANCELLED.

    Returns: True if this call performed the dissolution.
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

        # ⚠️ Must also invalidate pending payments.
        #
        # 1. Prevents `uq_one_pending_payment_per_seat` (Phase 11) from blocking
        #    new checkouts on these seats.
        # 2. Prevents users from completing a checkout for a dissolved group.
        db.execute(
            update(Payment)
            .where(
                Payment.group_share_id == share.id,
                Payment.status == PAYMENT_PENDING,
            )
            .values(status=PAYMENT_EXPIRED, failure_reason="group_broken")
            .execution_options(synchronize_session=False)
        )

        # Release seat only if it is still held by this group.
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

    logger.info("Group %s %s — %s seats released.", group.id, reason, len(shares))
    return True


def _refund_share(db: Session, share: GroupShare, payment: Payment | None) -> None:
    """
    Refunds a share.

    ⚠️ In the mock provider, this only updates the status. In production, this
    would trigger an API call to the gateway, requiring a `refund_pending`
    state to handle the asynchronous confirmation webhook.
    """
    share.status = SHARE_REFUNDED
    if payment is not None:
        payment.status = PAYMENT_REFUNDED
        payment.failure_reason = "group_broken"


def expire_due_groups(db: Session) -> int:
    """
    Dissolves all groups past their deadline.

    Triggered by a background job. Unlike "lazy cleanup" (which only runs when
    a user visits a page), this ensures refunds are processed promptly even if
    the user never returns to the site.
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
            db.rollback()
            logger.exception("Failed to expire group %s.", group.id)

    return broken
