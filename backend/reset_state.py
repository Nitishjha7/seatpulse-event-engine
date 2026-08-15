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

    # Sirf apni lock keys — flushall poora Redis udata hai, jo aage
    # (jab Redis me aur cheezein hongi) galat hoga.
    keys = list(redis_client.scan_iter("seat:*:lock"))
    if keys:
        redis_client.delete(*keys)
    print(f"✅ {len(keys)} Redis locks saaf kiye")


if __name__ == "__main__":
    reset()
