"""
Test data banata hai — 1 event + 100 seats (10 rows x 10 seats) + 1 user.

Chalao:
    docker compose exec backend python seed.py

Ye script dubara chalane par duplicate nahi banayega — pehle check karta hai.
"""

from datetime import timedelta

from sqlalchemy import select

from database import SessionLocal
from models import Event, Seat, User, utcnow

ROWS = "ABCDEFGHIJ"      # 10 rows
SEATS_PER_ROW = 10       # har row me 10 seats = 100 total

# Aage ki rows sasti, aage waali mehngi
PRICE_BY_ROW = {"A": 2500, "B": 2500, "C": 1800, "D": 1800, "E": 1200}
DEFAULT_PRICE = 800


def seed():
    db = SessionLocal()
    try:
        # ---- User ----
        user = db.scalar(select(User).where(User.email == "demo@seatpulse.dev"))
        if user is None:
            user = User(
                email="demo@seatpulse.dev",
                # Phase 5 me asli hashing (bcrypt) aayegi. Abhi placeholder.
                hashed_password="not-a-real-hash-yet",
                full_name="Demo User",
            )
            db.add(user)
            print("✅ Demo user banaya")
        else:
            print("ℹ️  Demo user pehle se hai")

        # ---- Event ----
        event = db.scalar(select(Event).where(Event.name == "Arijit Singh Live"))
        if event is None:
            event = Event(
                name="Arijit Singh Live",
                venue="DY Patil Stadium, Mumbai",
                starts_at=utcnow() + timedelta(days=30),
                total_seats=len(ROWS) * SEATS_PER_ROW,
            )
            db.add(event)
            db.flush()   # id chahiye seats banane ke liye, isliye flush
            print(f"✅ Event banaya (id={event.id})")
        else:
            print(f"ℹ️  Event pehle se hai (id={event.id})")

        # ---- Seats ----
        existing = db.scalar(
            select(Seat).where(Seat.event_id == event.id).limit(1)
        )
        if existing is None:
            seats = [
                Seat(
                    event_id=event.id,
                    row_label=row,
                    seat_number=num,
                    price=PRICE_BY_ROW.get(row, DEFAULT_PRICE),
                )
                for row in ROWS
                for num in range(1, SEATS_PER_ROW + 1)
            ]
            db.add_all(seats)
            print(f"✅ {len(seats)} seats banayi")
        else:
            print("ℹ️  Seats pehle se hain")

        db.commit()
        print("\n🎉 Seed complete")

    except Exception:
        # Kuch bhi galat ho to poora rollback — aadha-adhura data nahi chahiye
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
