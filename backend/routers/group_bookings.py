"""
Group booking routes.

Business logic resides in `groups.py`. This module handles HTTP concerns:
authentication, ownership, and mapping `GroupError` to appropriate status codes.

---- Access model ----

Groups are accessed via `share_token` rather than ID. This allows anyone with
the link to view and claim a share. Using sequential IDs (e.g., `/api/groups/1`)
would allow unauthorized access to other groups.

Login is required to claim a share to track ownership and ensure accountability.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth import get_current_user
from config import settings
from database import get_db
from groups import (
    DEFAULT_DEADLINE_MINUTES,
    GroupError,
    break_group,
    claim_share,
    create_group,
)
from models import (
    GROUP_CANCELLED,
    GROUP_COLLECTING,
    PAYMENT_PENDING,
    SHARE_UNPAID,
    GroupBooking,
    GroupShare,
    Payment,
    Seat,
    User,
    utcnow,
)
from payments import PaymentError, get_provider
from pricing_state import price_now
from rate_limit import BOOKING, limit_user
from routers.payments import _checkout_url_for
from schemas import CheckoutOut, GroupCreate, GroupOut, GroupShareOut

router = APIRouter(prefix="/api/groups", tags=["groups"])


def _to_out(db: Session, group: GroupBooking) -> GroupOut:
    shares = db.scalars(
        select(GroupShare).where(GroupShare.group_id == group.id).order_by(GroupShare.id)
    ).all()

    users = {}
    for share in shares:
        if share.claimed_by and share.claimed_by not in users:
            u = db.get(User, share.claimed_by)
            users[share.claimed_by] = (u.full_name or u.email.split("@")[0]) if u else "?"

    seats = {s.id: s for s in db.scalars(
        select(Seat).where(Seat.id.in_([sh.seat_id for sh in shares]))
    ).all()}

    remaining = int((group.expires_at - utcnow()).total_seconds())

    return GroupOut(
        share_token=group.share_token,
        event_id=group.event_id,
        status=group.status,
        expires_at=group.expires_at,
        # Used for frontend countdown. Can be negative if the deadline passed
        # but the cleanup job hasn't run. We show the actual remaining time
        # rather than "0" to avoid implying cleanup has already occurred.
        seconds_left=remaining,
        total_shares=len(shares),
        paid_shares=sum(1 for s in shares if s.status == "paid"),
        shares=[
            GroupShareOut(
                id=s.id,
                seat_id=s.seat_id,
                seat_label=f"{seats[s.seat_id].row_label}-{seats[s.seat_id].seat_number}"
                if s.seat_id in seats else "?",
                amount=float(s.amount),
                status=s.status,
                claimed_by=s.claimed_by,
                claimed_by_name=users.get(s.claimed_by),
            )
            for s in shares
        ],
    )


def _load(db: Session, share_token: str) -> GroupBooking:
    group = db.scalar(
        select(GroupBooking).where(GroupBooking.share_token == share_token)
    )
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")
    return group


@router.post(
    "",
    response_model=GroupOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limit_user(BOOKING))],
)
def create(
    payload: GroupCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Hold N seats and generate a shareable link."""
    seats = db.scalars(select(Seat).where(Seat.id.in_(payload.seat_ids))).all()
    if len(seats) != len(set(payload.seat_ids)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Some seats not found")

    if len({s.event_id for s in seats}) > 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "All seats must belong to the same event"
        )

    # Price is frozen at creation to protect against surge pricing changes
    # during the group booking window (Phase 14).
    quoted = {s.id: price_now(db, s) for s in seats}

    try:
        group = create_group(
            db,
            user=user,
            seat_ids=payload.seat_ids,
            deadline_minutes=payload.deadline_minutes or DEFAULT_DEADLINE_MINUTES,
            quoted=quoted,
        )
    except GroupError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))

    return _to_out(db, group)


@router.get("/{share_token}", response_model=GroupOut)
def get_group(
    share_token: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Retrieve group details via link."""
    return _to_out(db, _load(db, share_token))


@router.post("/{share_token}/shares/{share_id}/claim", response_model=GroupOut)
def claim(
    share_token: str,
    share_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Claim an available seat."""
    group = _load(db, share_token)
    if group.status != GROUP_COLLECTING:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Group status is '{group.status}'"
        )

    share = db.get(GroupShare, share_id)
    if share is None or share.group_id != group.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found")

    # Prevent a single user from claiming multiple seats in the same group.
    mine = db.scalar(
        select(GroupShare.id).where(
            GroupShare.group_id == group.id, GroupShare.claimed_by == user.id
        ).limit(1)
    )
    if mine is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You have already claimed a seat in this group"
        )

    try:
        claim_share(db, share, user)
    except GroupError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))

    return _to_out(db, group)


@router.delete("/{share_token}", response_model=GroupOut)
def cancel(
    share_token: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Cancel the group (creator only).

    Refunds are handled by `break_group`. This does not delete the record;
    it updates the status to maintain financial audit trails.
    """
    group = _load(db, share_token)
    if group.created_by != user.id:
        # Return 404 to maintain consistency with project-wide security patterns.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")

    if not break_group(db, group, GROUP_CANCELLED):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Group already settled"
        )

    db.refresh(group)
    return _to_out(db, group)


@router.get("", response_model=list[GroupOut])
def my_groups(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List groups created by or joined by the user."""
    joined = select(GroupShare.group_id).where(GroupShare.claimed_by == user.id)
    groups = db.scalars(
        select(GroupBooking)
        .where((GroupBooking.created_by == user.id) | (GroupBooking.id.in_(joined)))
        .order_by(GroupBooking.created_at.desc())
        .limit(20)
    ).all()
    return [_to_out(db, g) for g in groups]


@router.post(
    "/{share_token}/shares/{share_id}/pay",
    response_model=CheckoutOut,
    dependencies=[Depends(limit_user(BOOKING))],
)
def pay_share(
    share_token: str,
    share_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Create a checkout session for a specific share.

    ⚠️ Distinct from `/api/payments/checkout` which is for individual seats.
    Here, the seat is already held by the group. We do not re-claim the seat;
    we only process payment. The seat status remains `group_held` until the
    entire group is settled.
    """
    group = _load(db, share_token)
    if group.status != GROUP_COLLECTING:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Group status is '{group.status}'")

    if group.expires_at < utcnow():
        # Prevent payment if the deadline has passed but the cleanup job is pending.
        raise HTTPException(status.HTTP_409_CONFLICT, "Group deadline has passed")

    share = db.get(GroupShare, share_id)
    if share is None or share.group_id != group.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found")

    if share.claimed_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You have not claimed this seat")

    if share.status != SHARE_UNPAID:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Share status is '{share.status}'")

    # Return existing pending payment to prevent duplicate sessions.
    existing = db.scalar(
        select(Payment).where(
            Payment.group_share_id == share.id, Payment.status == PAYMENT_PENDING
        )
    )
    if existing is not None and existing.expires_at > utcnow():
        return CheckoutOut(
            payment_id=existing.id,
            checkout_url=_checkout_url_for(existing),
            amount=float(existing.amount),
            provider=existing.provider,
            expires_at=existing.expires_at,
        )

    provider = get_provider()
    payment = Payment(
        user_id=user.id,
        seat_id=share.seat_id,
        event_id=group.event_id,
        group_share_id=share.id,
        # Use the frozen amount from the group creation.
        amount=float(share.amount),
        currency=settings.CURRENCY,
        provider=provider.name,
        status=PAYMENT_PENDING,
        expires_at=min(
            utcnow() + timedelta(seconds=settings.PAYMENT_TTL_SECONDS),
            # Payment window cannot exceed group deadline.
            group.expires_at,
        ),
    )
    db.add(payment)
    db.flush()

    try:
        session = provider.create_checkout(
            payment_id=payment.id,
            amount=float(share.amount),
            description=f"Group share — seat {share.seat_id}",
        )
    except PaymentError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))

    payment.provider_ref = session.reference
    db.commit()
    db.refresh(payment)

    return CheckoutOut(
        payment_id=payment.id,
        checkout_url=_checkout_url_for(payment),
        amount=float(payment.amount),
        provider=payment.provider,
        expires_at=payment.expires_at,
    )
