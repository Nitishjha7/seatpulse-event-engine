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
# Kitne NUMBERED test users (user1 … userN). Named accounts iske alawa
# hain, isliye total = SEED_USERS + 3.
#
# 499 isliye ki loadtest/locustfile.py ka USER_POOL_SIZE bhi 499 hai —
# har concurrent Locust user ko apna account chahiye.
SEED_USERS = int(os.getenv("SEED_USERS", "499"))

ROWS = "ABCDEFGHIJ"      # 10 rows
SEATS_PER_ROW = 10       # har row me 10 seats = 100 total

# Aage ki rows sasti, aage waali mehngi
PRICE_BY_ROW = {"A": 2500, "B": 2500, "C": 1800, "D": 1800, "E": 1200}
DEFAULT_PRICE = 800


def seed():
    db = SessionLocal()
    try:
        # ---- Users ----
        #
        # Do tarah ke accounts:
        #   named   — demo / organizer / admin. Teeno roles test karne ke liye.
        #   numbered — user1 ... userN. Load test aur concurrency tests ke
        #              liye, jahan har concurrent client ka apna account
        #              chahiye hota hai.
        #
        # ⚠️ Numbering users ki GINTI se nahi banti.
        #
        # Pehle `range(existing, existing + to_create)` tha, jahan `existing`
        # named accounts ke baad 3 ho jata tha. Nateeja: fresh DB par
        # user3...user499 bante the aur **user1 aur user2 kabhi bante hi
        # nahi**. Tests unhi se login karte hain, to `tokens` fixture skip
        # ho jati thi — aur 35 tests SKIPPED hote hue bhi suite "green"
        # dikhti thi. CI me ye chup-chaap pass ho jata.
        #
        # Ab numbering fixed hai (hamesha user1..userN) aur hum sirf wahi
        # banate hain jo pehle se nahi hain. Isse seed idempotent bhi ho
        # jata hai — do baar chalao to duplicate nahi banenge.

        # Sab test users ka password ek hi hai. bcrypt slow hai (~100ms),
        # 500 baar hash karte to seed ek minute leta. Ek baar hash karke
        # sabko wahi de rahe hain — ye SIRF test data ke liye theek hai.
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

        # Named accounts pehle — inhe ORM se add karte hain taki `demo` ko
        # id=1 mile (frontend aur docs isi maante hain).
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
            # Ek hi bulk insert — 500 alag INSERT se bahut tez
            db.bulk_save_objects(new_numbered)
        db.flush()

        created = len(new_named) + len(new_numbered)
        total = len(have) + created
        print(f"✅ Users: {created} naye banaye, total {total}")
        print(f"   Numbered: user1 … user{SEED_USERS}")
        print(f"   Login: {DEMO_EMAIL} / {DEMO_PASSWORD}")
        print(f"          organizer@seatpulse.dev / {DEMO_PASSWORD}  (organizer)")
        print(f"          admin@seatpulse.dev     / {DEMO_PASSWORD}  (admin)")

        # ---- Event ----
        #
        # ⚠️ organizer_id set karna ZAROORI hai.
        #
        # Pehle ye chhoot gaya tha aur seeded event ka organizer NULL rehta
        # tha. Nateeja fresh DB par: organizer portal me event dikhta hi
        # nahi, aur gate check-in par 403 "Ye ticket tumhare event ka nahi
        # hai" milta tha (ownership check organizer_id se match karta hai).
        #
        # Purani DB me ye chhupa hua tha kyunki event portal se banaya gaya
        # tha. Sirf `docker compose down -v` ke baad dikha — yaani jab CI
        # jaisi clean state bani.
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
            db.flush()   # id chahiye seats banane ke liye, isliye flush
            print(f"✅ Event banaya (id={event.id})")
        else:
            # Purani DB me organizer chhoot gaya ho to yahin theek kar do,
            # taki seed dobara chalane se dikkat khud hat jaye.
            if event.organizer_id is None and organizer is not None:
                event.organizer_id = organizer.id
                print(f"🔧 Event ka organizer set kiya ({organizer.email})")
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
