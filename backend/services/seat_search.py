"""
Seat search — filters seats based on criteria.

No LLM in this file. Natural language parsing happens in `ai.py`, which
turns something like "3 seats together under 1500 near the stage" into
SeatFilters(quantity=3, together=True, max_price=1500, row_preference=
"front"); everything from there on is plain deterministic code — no
models, API calls, or randomness.

That split matters: LLM output is never raw SQL, only a validated Pydantic
object feeding parameterized queries, so prompt injection can produce bad
filters but not a data leak. It also means this search logic is fully
testable without an API key, and it keeps working — filters just stop
accepting natural language — if the model is down or rate-limited.
"""

from dataclasses import dataclass

from core.models import SEAT_AVAILABLE


@dataclass(frozen=True)
class SeatCandidate:
    """Represents a match — one or more contiguous seats."""

    seat_ids: list[int]
    row_label: str
    section: str | None
    seat_numbers: list[int]
    total_price: float

    @property
    def label(self) -> str:
        nums = self.seat_numbers
        if len(nums) == 1:
            return f"{self.row_label}-{nums[0]}"
        return f"{self.row_label}-{nums[0]}…{nums[-1]}"


def _aisle_positions(layout: dict | None) -> dict[str, set[int]]:
    """Maps row labels to seat numbers followed by an aisle."""
    out: dict[str, set[int]] = {}
    if not layout:
        return out
    for section in layout.get("sections", []):
        for row in section.get("rows", []):
            gaps = row.get("aisles_after") or []
            if gaps:
                out[str(row["label"]).upper()] = set(gaps)
    return out


def _runs(seats: list, quantity: int, aisles: set[int]) -> list[list]:
    """
    Finds all groups of `quantity` contiguous available seats in a row.
    Aisles break contiguity — seats 5 and 6 with a walkway between them
    aren't "together" even though the numbers are sequential, so this
    checks the layout's aisle data too. Otherwise search could suggest
    seats that are actually split apart at the venue.
    """
    out = []
    run: list = []

    for seat in seats:
        if run:
            prev = run[-1]
            broken = (
                seat.seat_number != prev.seat_number + 1     # Gap in seat numbers
                or prev.seat_number in aisles                 # Aisle separation
            )
            if broken:
                run = []

        run.append(seat)

        if len(run) >= quantity:
            out.append(run[-quantity:])

    return out


def _row_rank(row_label: str, preference: str | None) -> list[int]:
    """
    Sort key based on row preference. Row A is closest to the stage by
    convention, so "front" sorts ascending from A and "back" reverses it.
    Returns character codes so labels like "A" vs "A1" sort correctly.
    """
    sign = -1 if preference == "back" else 1
    return [sign * ord(c) for c in row_label]


def find(
    seats: list,
    *,
    quantity: int = 1,
    together: bool = True,
    min_price: float | None = None,
    max_price: float | None = None,
    section: str | None = None,
    row_preference: str | None = None,
    layout: dict | None = None,
    limit: int = 12,
) -> list[SeatCandidate]:
    """
    Filters seats based on provided criteria. Runs in-memory after the SQL
    query rather than doing "N contiguous seats" with window functions in
    SQL — with a 2000-seat-per-event cap, Python is plenty fast (ms range).
    Would need revisiting if seat counts ever hit 100k.
    """
    quantity = max(1, min(quantity, 10))

    usable = [s for s in seats if s.status == SEAT_AVAILABLE]

    # `price` is the base; current price is what's actually shown/charged.
    # Explicit `is None` check because free seats (price 0) are falsy, and
    # `or` would wrongly fall back to the base price.
    def price_of(seat) -> float:
        display = getattr(seat, "_display_price", None)
        return float(seat.price if display is None else display)

    if min_price is not None:
        usable = [s for s in usable if price_of(s) >= min_price]
    if max_price is not None:
        usable = [s for s in usable if price_of(s) <= max_price]
    if section:
        want = section.strip().lower()
        usable = [s for s in usable if (s.section or "").lower() == want]

    aisles = _aisle_positions(layout)

    by_row: dict[str, list] = {}
    for seat in usable:
        by_row.setdefault(seat.row_label, []).append(seat)

    candidates: list[SeatCandidate] = []

    for row_label, row_seats in by_row.items():
        row_seats.sort(key=lambda s: s.seat_number)

        if quantity == 1 or not together:
            # No contiguity required, so each seat is its own match. With
            # together=False and quantity > 1 we still return individual
            # seats rather than forcing a misleading group.
            groups = [[s] for s in row_seats]
        else:
            groups = _runs(row_seats, quantity, aisles.get(row_label.upper(), set()))

        for group in groups:
            candidates.append(
                SeatCandidate(
                    seat_ids=[s.id for s in group],
                    row_label=row_label,
                    section=group[0].section,
                    seat_numbers=[s.seat_number for s in group],
                    total_price=sum(price_of(s) for s in group),
                )
            )

    # Sort by price, or by row preference if specified.
    if row_preference in ("front", "back"):
        candidates.sort(key=lambda c: (_row_rank(c.row_label, row_preference), c.total_price))
    else:
        candidates.sort(key=lambda c: (c.total_price, c.row_label, c.seat_numbers[0]))

    return candidates[:limit]
