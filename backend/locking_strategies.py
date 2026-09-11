"""
Benchmarking seat claim strategies.

The project uses OPTIMISTIC locking by default. This file implements a 
PESSIMISTIC approach (`SELECT ... FOR UPDATE`) to compare performance 
under identical load.

---- Comparison Summary ----

    OPTIMISTIC  — "Try, then fail on conflict."
                  UPDATE ... WHERE version = <read_version>
                  Rowcount 0 indicates a conflict. Fails immediately.

    PESSIMISTIC — "Lock first, then process."
                  SELECT ... FOR UPDATE -> Locks the row; others wait
                  until the transaction commits.

---- Correctness ----

Both strategies prevent overselling. The comparison focuses on 
**behaviour under contention**:

    Optimistic   -> Losers return 409 immediately.
    Pessimistic  -> Losers queue up, only to find the seat taken 
                    upon acquisition, then return 409.

Optimistic provides "fail-fast" behavior, while pessimistic involves 
"wait-then-fail." Under high contention (e.g., 500 users for one seat), 
this difference is significant and is the primary metric for this benchmark.

---- ⚠️ Benchmark-only code ----

The pessimistic path is only reachable when `settings.BENCHMARK_MODE` 
is enabled. Production defaults to optimistic locking. See 
`routers/bookings.py` for the rationale.
"""

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from models import SEAT_AVAILABLE, SEAT_BOOKED, SEAT_LOCKED, Seat

OPTIMISTIC = "optimistic"
PESSIMISTIC = "pessimistic"

_CLAIMABLE = (SEAT_AVAILABLE, SEAT_LOCKED)

# Values to set upon booking; must be identical across strategies to 
# ensure valid benchmark comparisons.
_CLAIM_VALUES = {
    "status": SEAT_BOOKED,
    "locked_by": None,
    "locked_until": None,
    "held_price": None,
}


@dataclass
class ClaimResult:
    won: bool
    # Failure reasons differ by strategy; this distinction is central 
    # to the benchmark analysis.
    reason: str | None = None


def claim_optimistic(db: Session, seat_id: int, expected_version: int) -> ClaimResult:
    """
    Atomic UPDATE with no locks or waiting.

    The logic relies on the WHERE clause: the version must match the 
    previously read state. Only one of multiple parallel requests will 
    match; others will return 0 rows and fail immediately.

    This is "optimistic" concurrency: assuming no conflict, but 
    detecting and aborting if one occurs.
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
    Lock the row, verify state, then update.

    ⚠️ `with_for_update()` blocks. Requests wait here until the 
    transaction holding the lock commits or rolls back.

    Blocking consumes database connections. High contention (e.g., 500 
    users) can lead to pool exhaustion if the connection pool is smaller 
    than the number of waiting requests. (Similar to the issue identified 
    in Phase 7).

    Unlike the optimistic version, this does not strictly require a 
    `version` column for safety, as the row lock prevents concurrent 
    access. The version is still incremented to notify WebSocket clients 
    and maintain data consistency across strategies.
    """
    seat = db.execute(
        select(Seat).where(Seat.id == seat_id).with_for_update()
    ).scalar_one_or_none()

    if seat is None:
        return ClaimResult(False, "not_found")

    # ⭐ Check status after acquiring the lock.
    #
    # By the time this request acquires the lock, the previous holder 
    # may have already booked the seat. Re-verifying status is necessary 
    # and reliable because the row is now locked.
    if seat.status not in _CLAIMABLE:
        return ClaimResult(False, f"already_{seat.status}")

    seat.version += 1
    for field, value in _CLAIM_VALUES.items():
        setattr(seat, field, value)

    return ClaimResult(True)
