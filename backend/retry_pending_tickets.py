"""
Un bookings ke tickets dobara queue karo jo ban nahi paaye.

Zaroorat kyu: `enqueue_ticket()` jaan-boojh ke exceptions nigal jata hai —
Redis down ho to booking fail nahi honi chahiye. Par uska matlab ye bhi hai
ki kabhi-kabhi job queue hi nahi hota.

Aur worker apni saari retries ke baad `failed` mark kar deta hai.

Ye script dono uthata hai. Cron se har 10 minute chalao:
    docker compose exec backend python retry_pending_tickets.py

Yahi pattern hai jo `reconcile_payments.py` me hai — background kaam ka
fast path (queue) aur safety net (ye script). Dono chahiye.
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

# Booking banne ke itni der baad bhi ticket pending hai to kuch galat hai.
# Isse kam rakhte to abhi-abhi bani bookings ko dobara queue kar dete —
# jabki unka job queue me hi khada hai.
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
                    # Pending aur purani — matlab job kabhi queue hua hi nahi,
                    # ya worker uthhane se pehle mar gaya
                    (Booking.ticket_status == TICKET_PENDING)
                    & (Booking.created_at < cutoff),
                ),
            )
        ).all()

        if not stuck:
            logger.info("Sab tickets theek hain — kuch retry nahi karna")
            return

        for booking in stuck:
            booking.ticket_status = TICKET_PENDING
            enqueue_ticket(booking.id)
            logger.info("Re-queued booking %s (tha: %s)", booking.id, booking.ticket_status)

        db.commit()
        logger.info("✅ %d tickets dobara queue kiye", len(stuck))

    finally:
        db.close()


if __name__ == "__main__":
    retry()
