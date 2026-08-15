"""
Load test ke baad database ki sachai check karta hai.

Locust batata hai "kitni requests, kitna time". Ye batata hai
"data sahi raha ya nahi" — aur asli proof yahi hai.

Chalao:
    docker compose exec backend python verify_integrity.py
"""

import sys

from sqlalchemy import func, select

from database import SessionLocal
from models import BOOKING_CONFIRMED, SEAT_BOOKED, Booking, Seat


def check(label: str, ok: bool, detail: str = "") -> bool:
    icon = "✅" if ok else "❌"
    print(f"  {icon} {label}{('  — ' + detail) if detail else ''}")
    return ok


def main() -> int:
    db = SessionLocal()
    passed = True

    try:
        print("\n" + "=" * 62)
        print("INTEGRITY CHECK")
        print("=" * 62)

        # ---- 1. Sabse important: ek seat, ek confirmed booking ----
        # Agar overselling hui hoti, to yahan koi seat 2+ ke saath dikhti.
        oversold = db.execute(
            select(Booking.seat_id, func.count(Booking.id).label("n"))
            .where(Booking.status == BOOKING_CONFIRMED)
            .group_by(Booking.seat_id)
            .having(func.count(Booking.id) > 1)
        ).all()

        passed &= check(
            "Koi seat do baar nahi biki",
            not oversold,
            "" if not oversold else f"OVERSOLD: {[(s, n) for s, n in oversold]}",
        )

        # ---- 2. booked seats == confirmed bookings ----
        booked_seats = db.scalar(
            select(func.count(Seat.id)).where(Seat.status == SEAT_BOOKED)
        )
        confirmed = db.scalar(
            select(func.count(Booking.id)).where(Booking.status == BOOKING_CONFIRMED)
        )
        passed &= check(
            "Seat status aur bookings match karte hain",
            booked_seats == confirmed,
            f"{booked_seats} booked seats, {confirmed} confirmed bookings",
        )

        # ---- 3. Har booked seat ki booking honi chahiye ----
        orphan_seats = db.scalar(
            select(func.count(Seat.id))
            .outerjoin(
                Booking,
                (Booking.seat_id == Seat.id) & (Booking.status == BOOKING_CONFIRMED),
            )
            .where(Seat.status == SEAT_BOOKED, Booking.id.is_(None))
        )
        passed &= check(
            "Koi booked seat bina booking ke nahi",
            orphan_seats == 0,
            f"{orphan_seats} orphan seats",
        )

        # ---- 4. Har confirmed booking ki seat booked honi chahiye ----
        bad_bookings = db.scalar(
            select(func.count(Booking.id))
            .join(Seat, Seat.id == Booking.seat_id)
            .where(Booking.status == BOOKING_CONFIRMED, Seat.status != SEAT_BOOKED)
        )
        passed &= check(
            "Koi booking bina booked seat ke nahi",
            bad_bookings == 0,
            f"{bad_bookings} mismatched bookings",
        )

        # ---- Numbers ----
        print("-" * 62)
        rows = db.execute(
            select(Seat.status, func.count(Seat.id)).group_by(Seat.status)
        ).all()
        print("  Seats:    " + ", ".join(f"{s}={n}" for s, n in rows))

        rows = db.execute(
            select(Booking.status, func.count(Booking.id)).group_by(Booking.status)
        ).all()
        print("  Bookings: " + (", ".join(f"{s}={n}" for s, n in rows) or "koi nahi"))

        print("=" * 62)
        print("  " + ("✅ SAB PASS — koi overselling nahi hui" if passed
                      else "❌ FAIL — upar dekho"))
        print("=" * 62 + "\n")

        return 0 if passed else 1

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
