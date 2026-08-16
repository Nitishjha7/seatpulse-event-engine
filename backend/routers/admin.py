"""
Admin routes — poore platform ka overview.

Sirf `admin` role. Organizer ko bhi ye nahi dikhta — usse sirf apne
events ka data milta hai.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from auth import require_role
from database import get_db
from models import (
    BOOKING_CANCELLED,
    BOOKING_CONFIRMED,
    ROLE_ADMIN,
    ROLE_ORGANIZER,
    Booking,
    Event,
    Seat,
    User,
)
from redis_client import redis_client
from schemas import AdminStatsOut
from websocket import manager

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/stats", response_model=AdminStatsOut)
def platform_stats(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(ROLE_ADMIN)),
):
    """
    Platform ke live numbers.

    Data teen jagah se aata hai:
      Postgres — users, events, bookings, revenue
      Redis    — abhi kitni seats hold me hain
      Memory   — is worker pe kitne WebSocket clients

    ⚠️ `live_connections` sirf IS worker ka count hai. Multi-worker
    deployment me har worker apna alag number dega. Sahi total ke liye
    ye number bhi Redis me rakhna padega — abhi wo zaroorat nahi hai,
    par ye limitation jaan-boojh ke bata rahe hain.
    """
    booking_counts = dict(
        db.execute(
            select(Booking.status, func.count(Booking.id)).group_by(Booking.status)
        ).all()
    )

    revenue = db.scalar(
        select(func.coalesce(func.sum(Booking.amount), 0)).where(
            Booking.status == BOOKING_CONFIRMED
        )
    )

    # scan_iter — KEYS ka istemaal production me kabhi nahi karna chahiye,
    # wo poore Redis ko block kar deta hai. scan cursor-based hai.
    active_locks = sum(1 for _ in redis_client.scan_iter("seat:*:lock"))

    live = sum(manager.count(event_id) for event_id in manager.rooms())

    return AdminStatsOut(
        users=db.scalar(select(func.count(User.id))),
        organizers=db.scalar(
            select(func.count(User.id)).where(User.role == ROLE_ORGANIZER)
        ),
        events=db.scalar(select(func.count(Event.id))),
        seats=db.scalar(select(func.count(Seat.id))),
        bookings_confirmed=booking_counts.get(BOOKING_CONFIRMED, 0),
        bookings_cancelled=booking_counts.get(BOOKING_CANCELLED, 0),
        revenue=float(revenue),
        active_locks=active_locks,
        live_connections=live,
    )
