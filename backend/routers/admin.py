"""
Admin routes providing a platform-wide overview.

Restricted to `admin` role. Organizers are excluded, as they only have access to their own event data.
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
    Live platform metrics.

    Data sources:
      Postgres — users, events, bookings, revenue
      Redis    — current seat holds
      Memory   — active WebSocket clients on this worker

    ⚠️ `live_connections` reflects only the current worker. In multi-worker
    deployments, this value is local to each instance. Global totals would
    require Redis-based tracking; this limitation is intentional for now.
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

    # scan_iter — Avoid using KEYS in production to prevent blocking the Redis
    # event loop. scan provides a cursor-based, non-blocking alternative.
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
