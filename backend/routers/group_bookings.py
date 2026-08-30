"""
Group booking ke routes.

Business logic `groups.py` me hai — yahan sirf HTTP ka kaam hai:
auth, ownership, aur GroupError ko sahi status code me badalna.

---- Access model ----

Group ko `share_token` se dhoondhte hain, id se nahi. Jiske paas link hai
wo dekh sakta hai aur ek khaali share le sakta hai. Sequential id se
dhoondhte to koi bhi `/api/groups/1`, `/2`, `/3` chala ke doosron ke groups
me ghus jata.

Login phir bhi zaroori hai — share claim karne ke liye pata hona chahiye
ki kaun le raha hai, aur baad me ticket kiska hai.
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
        # Frontend countdown isse chalata hai. Negative bhi ho sakta hai —
        # deadline nikal chuki ho par expiry job abhi na chala ho. Wo
        # honesty jaan-boojh ke hai: hum "0" dikha ke ye nahi jataana chahte
        # ki cleanup ho chuka hai jab wo abhi hua hi nahi.
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group nahi mila")
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
    """N seats hold karo aur shareable link banao."""
    seats = db.scalars(select(Seat).where(Seat.id.in_(payload.seat_ids))).all()
    if len(seats) != len(set(payload.seat_ids)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kuch seats nahi mili")

    if len({s.event_id for s in seats}) > 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Saari seats ek hi event ki honi chahiye"
        )

    # Price ab freeze ho jata hai — group me 30 minute lag sakte hain aur
    # us beech surge badal sakta hai (Phase 14). Jo quote kiya wahi lagega.
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
    """Link se group dekho — kisne kya liya, kitna paisa aaya, kitna time bacha."""
    return _to_out(db, _load(db, share_token))


@router.post("/{share_token}/shares/{share_id}/claim", response_model=GroupOut)
def claim(
    share_token: str,
    share_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Ek khaali seat apne naam karo."""
    group = _load(db, share_token)
    if group.status != GROUP_COLLECTING:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Ye group ab '{group.status}' hai"
        )

    share = db.get(GroupShare, share_id)
    if share is None or share.group_id != group.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share nahi mila")

    # Ek banda ek hi seat le — warna ek hi user poora group claim kar leta
    # aur "split" ka matlab hi khatam
    mine = db.scalar(
        select(GroupShare.id).where(
            GroupShare.group_id == group.id, GroupShare.claimed_by == user.id
        ).limit(1)
    )
    if mine is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Tumne is group me pehle se ek seat li hui hai"
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
    Group cancel karo — sirf banane wala.

    Jo paise aa chuke hain wo refund ho jaate hain (`break_group` sambhalta
    hai). Isliye ye "delete" nahi hai — record rehta hai, sirf status badalta
    hai. Paise ke record kabhi delete nahi karte.
    """
    group = _load(db, share_token)
    if group.created_by != user.id:
        # 404, 403 nahi — wahi pattern jo baaki project me hai
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group nahi mila")

    if not break_group(db, group, GROUP_CANCELLED):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Group already settle ho chuka hai"
        )

    db.refresh(group)
    return _to_out(db, group)


@router.get("", response_model=list[GroupOut])
def my_groups(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Mere banaye hue + jinme maine seat li hai."""
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
    Apne hisse ka checkout session banao.

    ⚠️ Ye `/api/payments/checkout` se alag hai kyunki wo SEAT ke liye hai.
    Yahan seat pehle se group ke hold me hai — hume usse dobara claim nahi
    karna, sirf paisa lena hai. Isliye `Payment.seat_id` set hota hai par
    seat ka status **nahi** badalta: wo `group_held` hi rehta hai jab tak
    poora group settle na ho.
    """
    group = _load(db, share_token)
    if group.status != GROUP_COLLECTING:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Ye group ab '{group.status}' hai")

    if group.expires_at < utcnow():
        # Deadline nikal chuki hai par expiry job abhi nahi chala. Paisa
        # lena galat hoga — refund karna padta.
        raise HTTPException(status.HTTP_409_CONFLICT, "Group ki deadline nikal chuki hai")

    share = db.get(GroupShare, share_id)
    if share is None or share.group_id != group.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share nahi mila")

    if share.claimed_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Ye seat tumne claim nahi ki")

    if share.status != SHARE_UNPAID:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Ye hissa already '{share.status}' hai")

    # Pehle se ek pending payment hai? Wahi wapas do — double click par do
    # sessions nahi banne chahiye.
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
        # Amount share se aata hai, seat se nahi — wo group banate waqt
        # freeze hua tha aur wahi waada hai.
        amount=float(share.amount),
        currency=settings.CURRENCY,
        provider=provider.name,
        status=PAYMENT_PENDING,
        expires_at=min(
            utcnow() + timedelta(seconds=settings.PAYMENT_TTL_SECONDS),
            # Payment window group deadline se aage nahi ja sakta — warna
            # user deadline ke baad pay kar deta aur seedha refund me jata
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
