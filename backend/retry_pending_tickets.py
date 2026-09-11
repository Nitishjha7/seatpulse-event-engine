"""
Re-queue tickets for bookings that failed to process.

Rationale: `enqueue_ticket()` suppresses exceptions to ensure booking flows
succeed even if Redis is unavailable. Consequently, some jobs may fail to
enter the queue. Additionally, workers mark jobs as `failed` after exhausting
retries.

This script recovers both cases. Run via cron every 10 minutes:
    docker compose exec backend python retry_pending_tickets.py

This follows the pattern used in `reconcile_payments.py` — combining a fast
path (queue) with a safety net (this script) for background tasks.
"""

import logging
from datetime import timedelta

from sqlalchemy import or_, select

from database import SessionLocal
from job_queue import enqueue_ticket
from models import (
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
