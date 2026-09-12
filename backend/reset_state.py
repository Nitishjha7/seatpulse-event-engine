"""
Resets the system state: clears all bookings, restores seat availability, and removes locks.

Used frequently during testing.

Usage:
    docker compose exec backend python reset_state.py
"""

from sqlalchemy import delete, update

from database import SessionLocal
from models import SEAT_AVAILABLE, Booking, Payment, Seat
from redis_client import redis_client


def reset():
    db = SessionLocal()
    try:
        # Delete payments first to satisfy Booking FK constraints.
        payments = db.execute(delete(Payment)).rowcount
        bookings = db.execute(delete(Booking)).rowcount
        seats = db.execute(
            update(Seat).values(
                status=SEAT_AVAILABLE,
                locked_by=None,
                locked_until=None,
                version=0,
            )
        ).rowcount
        db.commit()
        print(f"✅ {payments} payments, {bookings} bookings removed, {seats} seats set to available")
    finally:
        db.close()

    # Use pattern-based deletion instead of `flushall`.
    #
    # `flushall` would clear refresh tokens, forcing testers to re-authenticate.
    # Pattern-based deletion preserves session state.
    for pattern, label in [
        ("seat:*:lock", "seat locks"),
        ("rl:*", "rate limit buckets"),
        ("idem:*", "idempotency keys"),
    ]:
        keys = list(redis_client.scan_iter(pattern))
        if keys:
            redis_client.delete(*keys)
        print(f"✅ {len(keys)} {label} cleared")


if __name__ == "__main__":
    reset()
