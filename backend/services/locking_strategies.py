"""
Benchmarking seat claim strategies.

The app uses OPTIMISTIC locking by default. This file adds a PESSIMISTIC
path (`SELECT ... FOR UPDATE`) to compare performance under identical load.

    OPTIMISTIC  — try, then fail on conflict: UPDATE ... WHERE version =
                  <read_version>. Rowcount 0 means someone else won; fails
                  immediately.
    PESSIMISTIC — lock first, then process: SELECT ... FOR UPDATE blocks
                  everyone else until the transaction commits.

Both prevent overselling, so the interesting difference is behavior under
contention: optimistic losers get a 409 right away, pessimistic losers queue
up and only find out the seat's gone once they reach the front. Fail-fast
vs. wait-then-fail — with 500 users on one seat, that gap is the whole point
of this benchmark.

The pessimistic path only runs when `settings.BENCHMARK_MODE` is on;
production always uses optimistic locking (see `routers/bookings.py`).
"""

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core.models import SEAT_AVAILABLE, SEAT_BOOKED, SEAT_LOCKED, Seat

OPTIMISTIC = "optimistic"
PESSIMISTIC = "pessimistic"

_CLAIMABLE = (SEAT_AVAILABLE, SEAT_LOCKED)

# Values to set upon booking; must match across strategies for a fair comparison.
_CLAIM_VALUES = {
    "status": SEAT_BOOKED,
    "locked_by": None,
    "locked_until": None,
    "held_price": None,
}


@dataclass
class ClaimResult:
    won: bool
    # Failure reasons differ by strategy — that's the core of the analysis.
    reason: str | None = None


def claim_optimistic(db: Session, seat_id: int, expected_version: int) -> ClaimResult:
    """
    Atomic UPDATE, no locks or waiting. The WHERE clause requires the
    version to match what was last read, so only one of several parallel
    requests matches — the rest get 0 rows and fail immediately. Assumes no
    conflict, detects and aborts if there was one.
    """
    result = db.execute(
        update(Seat)
        .where(
            Seat.id == seat_id,
            Seat.version == expected_version,
            Seat.status.in_(_CLAIMABLE),
        )
        .values(version=Seat.version + 1, **_CLAIM_VALUES)
        .execution_options(synchronize_session=False)
    )

    if result.rowcount == 0:
        return ClaimResult(False, "version_conflict")
    return ClaimResult(True)


def claim_pessimistic(db: Session, seat_id: int) -> ClaimResult:
    """
    Lock the row, verify state, then update. `with_for_update()` blocks —
    requests wait here until the transaction holding the lock commits or
    rolls back.

    Blocking holds a database connection open, so high contention (say 500
    users on one seat) can exhaust the pool if it's smaller than the number
    of waiting requests — the same class of problem as a slow bcrypt call
    holding a connection during login.

    Doesn't strictly need the `version` column for safety since the row
    lock already prevents concurrent access, but it's still bumped to
    notify WebSocket clients and keep data consistent across strategies.
    """
    seat = db.execute(
        select(Seat).where(Seat.id == seat_id).with_for_update()
    ).scalar_one_or_none()

    if seat is None:
        return ClaimResult(False, "not_found")

    # Re-check status after acquiring the lock — the previous holder may
    # have already booked the seat by the time we got here, and the row
    # lock makes this check reliable now.
    if seat.status not in _CLAIMABLE:
        return ClaimResult(False, f"already_{seat.status}")

    seat.version += 1
    for field, value in _CLAIM_VALUES.items():
        setattr(seat, field, value)

    return ClaimResult(True)
