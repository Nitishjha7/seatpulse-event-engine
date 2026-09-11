"""
Creates test data: 1 event + 100 seats (10 rows x 10 seats) + 1 user.

Usage:
    docker compose exec backend python seed.py

This script is idempotent; it checks for existing data before creating new records.
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

# Demo login credentials as specified in README and documentation.
DEMO_EMAIL = "demo@seatpulse.dev"
DEMO_PASSWORD = "demo1234"

# Number of test users to generate.
# Load testing requires unique user_ids for each concurrent user to avoid
# "already_owned" errors and accurately simulate contention.
# Total users = SEED_USERS + 3 (for named accounts).
# 499 is used to match the USER_POOL_SIZE in loadtest/locustfile.py.
SEED_USERS = int(os.getenv("SEED_USERS", "499"))

ROWS = "ABCDEFGHIJ"      # 10 rows
SEATS_PER_ROW = 10       # 10 seats per row = 100 total

# Pricing tiers by row.
PRICE_BY_ROW = {"A": 2500, "B": 2500, "C": 1800, "D": 1800, "E": 1200}
DEFAULT_PRICE = 800


def seed():
    db = SessionLocal()
    try:
        # ---- Users ----
        #
        # Two types of accounts:
        #   named    — demo / organizer / admin for role-based testing.
        #   numbered — user1 ... userN for load and concurrency testing.
        #
        # ⚠️ Numbering is fixed to ensure consistency.
        #
        # Previously, dynamic ranges caused user1 and user2 to be skipped,
        # breaking tests that relied on those specific accounts.
        # Fixed numbering ensures idempotency and consistent test state.

        # Bcrypt is slow (~100ms); hashing once and reusing for all test users
        # significantly speeds up the seeding process.
        shared_hash = hash_password(DEMO_PASSWORD)

        named = [
            (DEMO_EMAIL, "Demo User", ROLE_ATTENDEE),
            ("organizer@seatpulse.dev", "Demo Organizer", ROLE_ORGANIZER),
            ("admin@seatpulse.dev", "Demo Admin", ROLE_ADMIN),
        ]
        numbered = [
            (f"user{i}@seatpulse.dev", f"Test User {i}", ROLE_ATTENDEE)
            for i in range(1, SEED_USERS + 1)
        ]

        have = set(db.scalars(select(User.email)).all())

        # Add named accounts first to ensure the demo user receives id=1.
        new_named = [
            User(email=e, hashed_password=shared_hash, full_name=n, role=r)
            for e, n, r in named
            if e not in have
        ]
        if new_named:
            db.add_all(new_named)
            db.flush()

        new_numbered = [
            User(email=e, hashed_password=shared_hash, full_name=n, role=r)
            for e, n, r in numbered
            if e not in have
        ]
        if new_numbered:
            # Bulk insert is significantly faster than individual INSERTs.
            db.bulk_save_objects(new_numbered)
        db.flush()

        created = len(new_named) + len(new_numbered)
        total = len(have) + created
        print(f"✅ Users: {created} created, total {total}")
        print(f"   Numbered: user1 … user{SEED_USERS}")
        print(f"   Login: {DEMO_EMAIL} / {DEMO_PASSWORD}")
        print(f"          organizer@seatpulse.dev / {DEMO_PASSWORD}  (organizer)")
        print(f"          admin@seatpulse.dev     / {DEMO_PASSWORD}  (admin)")

        # ---- Event ----
        #
        # ⚠️ organizer_id is mandatory.
        #
        # Missing organizer_id causes 403 errors during gate check-in and
        # prevents events from appearing in the organizer portal.
        organizer = db.scalar(
            select(User).where(User.email == "organizer@seatpulse.dev")
        )

        event = db.scalar(select(Event).where(Event.name == "Arijit Singh Live"))
        if event is None:
            event = Event(
                organizer_id=organizer.id if organizer else None,
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
            db.flush()   # Flush to retrieve event.id for seat creation
            print(f"✅ Event created (id={event.id})")
        else:
            # Repair missing organizer_id in existing records.
            if event.organizer_id is None and organizer is not None:
                event.organizer_id = organizer.id
                print(f"🔧 Event organizer updated ({organizer.email})")
            print(f"ℹ️  Event already exists (id={event.id})")

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
            print(f"✅ {len(seats)} seats created")
        else:
            print("ℹ️  Seats already exist")

        db.commit()
        print("\n🎉 Seed complete")

    except Exception:
        # Rollback on failure to prevent partial data state.
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
