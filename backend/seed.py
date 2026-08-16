"""
Test data banata hai — 1 event + 100 seats (10 rows x 10 seats) + 1 user.

Chalao:
    docker compose exec backend python seed.py

Ye script dubara chalane par duplicate nahi banayega — pehle check karta hai.
"""

import os
from datetime import timedelta

from sqlalchemy import func, select

from auth import hash_password
from database import SessionLocal
from models import (
    ROLE_ADMIN,
    ROLE_ATTENDEE,
    ROLE_ORGANIZER,
    Event,
    Seat,
    User,
    utcnow,
)

# Demo login — README aur docs me yahi likha hai
DEMO_EMAIL = "demo@seatpulse.dev"
DEMO_PASSWORD = "demo1234"

# Kitne test users banane hain.
# Load test me har concurrent user ka apna user_id hona chahiye — warna
# same user dubara lock maange to "already_owned" wala 200 mil jata hai
# aur contention ki asli tasveer nahi banti.
SEED_USERS = int(os.getenv("SEED_USERS", "500"))

ROWS = "ABCDEFGHIJ"      # 10 rows
SEATS_PER_ROW = 10       # har row me 10 seats = 100 total

# Aage ki rows sasti, aage waali mehngi
PRICE_BY_ROW = {"A": 2500, "B": 2500, "C": 1800, "D": 1800, "E": 1200}
DEFAULT_PRICE = 800


def seed():
    db = SessionLocal()
    try:
        # ---- Users ----
        # id=1 hamesha demo user (frontend isi ko use karta hai).
        # Baaki load testing ke liye.
        existing = db.scalar(select(func.count(User.id)))

        # Sab test users ka password ek hi hai. bcrypt slow hai (~100ms),
        # 500 baar hash karte to seed ek minute leta. Ek baar hash karke
        # sabko wahi de rahe hain — ye SIRF test data ke liye theek hai.
        shared_hash = hash_password(DEMO_PASSWORD)

        if existing == 0:
            # Teeno roles ka ek-ek demo account — RBAC test karne ke liye
            db.add_all(
                [
                    User(
                        email=DEMO_EMAIL,
                        hashed_password=shared_hash,
                        full_name="Demo User",
                        role=ROLE_ATTENDEE,
                    ),
                    User(
                        email="organizer@seatpulse.dev",
                        hashed_password=shared_hash,
                        full_name="Demo Organizer",
                        role=ROLE_ORGANIZER,
                    ),
                    User(
                        email="admin@seatpulse.dev",
                        hashed_password=shared_hash,
                        full_name="Demo Admin",
                        role=ROLE_ADMIN,
                    ),
                ]
            )
            db.flush()
            existing = 3

        # Ek hi bulk insert — 500 alag INSERT se bahut tez
        to_create = max(0, SEED_USERS - existing)
        if to_create:
            db.bulk_save_objects(
                [
                    User(
                        email=f"user{i}@seatpulse.dev",
                        hashed_password=shared_hash,
                        full_name=f"Test User {i}",
                    )
                    for i in range(existing, existing + to_create)
                ]
            )
        db.flush()
        print(f"✅ Users: {to_create} naye banaye, total {SEED_USERS}")
        print(f"   Login: {DEMO_EMAIL} / {DEMO_PASSWORD}")
        print(f"          organizer@seatpulse.dev / {DEMO_PASSWORD}  (organizer)")
        print(f"          admin@seatpulse.dev     / {DEMO_PASSWORD}  (admin)")

        # ---- Event ----
        event = db.scalar(select(Event).where(Event.name == "Arijit Singh Live"))
        if event is None:
            event = Event(
                name="Arijit Singh Live",
                venue="DY Patil Stadium, Mumbai",
                starts_at=utcnow() + timedelta(days=30),
                total_seats=len(ROWS) * SEATS_PER_ROW,
                category="Music",
                description=(
                    "Experience the magical voice of Arijit Singh live in concert. "
                    "A night filled with soulful music, unforgettable moments and "
                    "pure emotions.\n\n"
                    "Gates open 90 minutes before showtime. Seats are held for "
                    "5 minutes once selected — confirm your booking before the "
                    "timer runs out."
                ),
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
