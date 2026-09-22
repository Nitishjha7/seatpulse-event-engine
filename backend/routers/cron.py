"""
Manual job runner, for deployments with no persistent worker process.

The ARQ worker (workers/worker.py) is the primary path — it's what runs
in docker-compose and processes jobs the moment they're queued. Some free
hosting tiers only offer a web service and have nowhere to run an
always-on worker at all, so this exposes the same two jobs (ticket
generation, group expiry) behind an endpoint an external scheduler can
call instead — a free Cloudflare Worker Cron Trigger hitting this every
couple of minutes is enough to keep tickets and expired groups from
piling up without needing a worker dyno running 24/7.

Not for interactive use — the caller here is a scheduler, not a person,
so this checks a shared secret rather than a user login.
"""

import logging

from fastapi import APIRouter, Header, HTTPException, status
from sqlalchemy import or_, select

from core.config import settings
from core.database import SessionLocal
from services.groups import expire_due_groups
from core.models import BOOKING_CONFIRMED, TICKET_FAILED, TICKET_PENDING, Booking
from workers.worker import generate_ticket_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cron", tags=["cron"])

# Caps how many tickets one call generates, so a large backlog can't turn
# a single request into a multi-minute one that times out at the edge.
# Anything left over just gets picked up on the next tick.
MAX_TICKETS_PER_TICK = 20


@router.post("/tick")
def run_pending_jobs(x_cron_secret: str | None = Header(None)):
    """Process pending tickets and expire overdue groups. Idempotent — safe to call repeatedly."""
    if not settings.CRON_SECRET or x_cron_secret != settings.CRON_SECRET:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing cron secret")

    db = SessionLocal()
    try:
        pending = db.scalars(
            select(Booking.id)
            .where(
                Booking.status == BOOKING_CONFIRMED,
                or_(Booking.ticket_status == TICKET_PENDING, Booking.ticket_status == TICKET_FAILED),
            )
            .limit(MAX_TICKETS_PER_TICK)
        ).all()
    finally:
        db.close()

    tickets_done = 0
    for booking_id in pending:
        try:
            generate_ticket_sync(booking_id)
            tickets_done += 1
        except Exception:
            logger.warning("cron tick: ticket generation failed for booking %s", booking_id, exc_info=True)

    db = SessionLocal()
    try:
        groups_expired = expire_due_groups(db)
    finally:
        db.close()

    return {"tickets_processed": tickets_done, "groups_expired": groups_expired}
