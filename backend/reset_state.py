"""
Sab kuch fresh — saari bookings hatao, saari seats available karo, locks saaf.

Testing ke beech me bar-bar chahiye hota hai.

Chalao:
    docker compose exec backend python reset_state.py
"""

from sqlalchemy import delete, update

from database import SessionLocal
from models import SEAT_AVAILABLE, Booking, Seat
from redis_client import redis_client


def reset():
    db = SessionLocal()
    try:
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
        print(f"✅ {bookings} bookings hataye, {seats} seats available ki")
    finally:
        db.close()

    # Pattern-wise delete karte hain, `flushall` nahi.
    #
    # flushall REFRESH TOKENS bhi uda deta — matlab har tester ka logout
    # ho jata testing ke beech me. Ab wo bache rehte hain.
    for pattern, label in [
        ("seat:*:lock", "seat locks"),
        ("rl:*", "rate limit buckets"),
        ("idem:*", "idempotency keys"),
    ]:
        keys = list(redis_client.scan_iter(pattern))
        if keys:
            redis_client.delete(*keys)
        print(f"✅ {len(keys)} {label} saaf kiye")


if __name__ == "__main__":
    reset()
