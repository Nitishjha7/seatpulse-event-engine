"""
Re-queue tickets for bookings that failed to process.

`enqueue_ticket()` swallows exceptions so a Redis outage doesn't break the
booking flow, which means some jobs never make it into the queue. Workers
also mark jobs `failed` once retries are exhausted. This script recovers
both cases — same fast-path-plus-safety-net pattern as reconcile_payments.py.

Run via cron every 10 minutes:
    docker compose exec backend python retry_pending_tickets.py
"""

import logging
from datetime import timedelta

from sqlalchemy import or_, select

from core.database import SessionLocal
from workers.job_queue import enqueue_ticket
from core.models import (
    BOOKING_CONFIRMED,
    TICKET_FAILED,
    TICKET_PENDING,
    Booking,
    utcnow,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("retry-tickets")

# Threshold to identify stale tickets. Prevents re-queuing active jobs
# that are still being processed by the queue.
STALE_AFTER = timedelta(minutes=2)


def retry() -> None:
    db = SessionLocal()
    try:
        cutoff = utcnow() - STALE_AFTER

        stuck = db.scalars(
            select(Booking).where(
                Booking.status == BOOKING_CONFIRMED,
                or_(
                    Booking.ticket_status == TICKET_FAILED,
                    # Pending and stale — indicates the job was never queued
                    # or the worker crashed before processing.
                    (Booking.ticket_status == TICKET_PENDING)
                    & (Booking.created_at < cutoff),
                ),
            )
        ).all()

        if not stuck:
            logger.info("All tickets are healthy; no retries required.")
            return

        for booking in stuck:
            booking.ticket_status = TICKET_PENDING
            enqueue_ticket(booking.id)
            logger.info("Re-queued booking %s (previous status: %s)", booking.id, booking.ticket_status)

        db.commit()
        logger.info("✅ %d tickets re-queued successfully.", len(stuck))

    finally:
        db.close()


if __name__ == "__main__":
    retry()
