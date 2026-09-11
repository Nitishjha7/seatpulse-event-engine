"""
Verifies database integrity post-load testing.

While Locust measures throughput and latency, this script validates data
consistency, which is the ultimate proof of correctness.

Usage:
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

        # ---- 1. Critical: Ensure no seat has multiple confirmed bookings ----
        # Detects overselling by identifying seats associated with >1 confirmed booking.
        oversold = db.execute(
            select(Booking.seat_id, func.count(Booking.id).label("n"))
            .where(Booking.status == BOOKING_CONFIRMED)
            .group_by(Booking.seat_id)
            .having(func.count(Booking.id) > 1)
        ).all()

        passed &= check(
            "No seat has multiple confirmed bookings",
            not oversold,
            "" if not oversold else f"OVERSOLD: {[(s, n) for s, n in oversold]}",
        )

        # ---- 2. Verify count of booked seats matches confirmed bookings ----
        booked_seats = db.scalar(
            select(func.count(Seat.id)).where(Seat.status == SEAT_BOOKED)
        )
        confirmed = db.scalar(
            select(func.count(Booking.id)).where(Booking.status == BOOKING_CONFIRMED)
        )
        passed &= check(
            "Seat status matches confirmed booking count",
            booked_seats == confirmed,
            f"{booked_seats} booked seats, {confirmed} confirmed bookings",
        )

        # ---- 3. Ensure every booked seat has a corresponding confirmed booking ----
        orphan_seats = db.scalar(
            select(func.count(Seat.id))
            .outerjoin(
                Booking,
                (Booking.seat_id == Seat.id) & (Booking.status == BOOKING_CONFIRMED),
            )
            .where(Seat.status == SEAT_BOOKED, Booking.id.is_(None))
        )
        passed &= check(
            "No booked seat exists without a confirmed booking",
            orphan_seats == 0,
            f"{orphan_seats} orphan seats",
        )

        # ---- 4. Ensure every confirmed booking maps to a booked seat ----
        bad_bookings = db.scalar(
            select(func.count(Booking.id))
            .join(Seat, Seat.id == Booking.seat_id)
            .where(Booking.status == BOOKING_CONFIRMED, Seat.status != SEAT_BOOKED)
        )
        passed &= check(
            "No confirmed booking exists without a booked seat",
            bad_bookings == 0,
            f"{bad_bookings} mismatched bookings",
        )

        # ---- Statistics ----
        print("-" * 62)
        rows = db.execute(
            select(Seat.status, func.count(Seat.id)).group_by(Seat.status)
        ).all()
        print("  Seats:    " + ", ".join(f"{s}={n}" for s, n in rows))

        rows = db.execute(
            select(Booking.status, func.count(Booking.id)).group_by(Booking.status)
        ).all()
        print("  Bookings: " + (", ".join(f"{s}={n}" for s, n in rows) or "none"))

        print("=" * 62)
        print("  " + ("✅ PASSED — no overselling detected" if passed
                      else "❌ FAILED — check details above"))
        print("=" * 62 + "\n")

        return 0 if passed else 1

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
