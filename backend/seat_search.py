"""
Seat search — filters seats based on criteria.

---- NO LLM HERE ----

Natural language processing is handled in `ai.py`, which performs:

    "3 seats together under 1500 near the stage"
                    |
                    v
    SeatFilters(quantity=3, together=True, max_price=1500,
                row_preference="front")

All subsequent processing occurs here using standard, deterministic code.
There are no models, API calls, or randomness involved.

This separation is a deliberate design choice:

  1. **Security.** LLM output is never used as raw SQL. It is mapped to a
     validated Pydantic object, and queries remain parameterised. Prompt
     injection can only produce invalid filters, not data leaks or SQL injection.

  2. **Testability.** The search logic is fully testable without API keys.
     None of the 90+ tests require Gemini.

  3. **Reliability.** The search remains functional even if the model is
     down or rate-limited. Only natural language input is disabled;
     standard filters continue to work.
"""

from dataclasses import dataclass

from models import SEAT_AVAILABLE


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

    ⚠️ Aisles break "together" status.

    If an aisle exists between seat 5 and 6, they are not considered
    contiguous, as they are separated by a walkway. Sequential numbering
    alone is insufficient; this logic relies on Phase 18 layout data.

    Without this check, the search might suggest "contiguous" seats that
    are physically separated, leading to poor user experience at the venue.
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
    Sort key based on row preference.

    Row A is closest to the stage (Phase 3 convention). "front" sorts
    ascending from A; "back" reverses this.

    Returns a list of character codes to ensure correct sorting of labels
    like "A" vs "A1".
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
    Filters seats based on provided criteria.

    Processing occurs in-memory after the initial SQL query. Reason:
    calculating "N contiguous available seats" in SQL is complex with
    window functions. Given the limit of 2000 seats per event, Python
    processing is significantly faster (milliseconds).

    This approach may need re-evaluation if seat counts reach 100k, but
    currently, we avoid premature optimization.
    """
    quantity = max(1, min(quantity, 10))

    usable = [s for s in seats if s.status == SEAT_AVAILABLE]

    # `price` is the base; current price is used for display (Phase 14).
    #
    # ⚠️ Use `is None` check; free seats (price 0) are falsy, and `or`
    # would incorrectly revert to the base price.
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
            # Contiguity not required; treat each seat as an individual match.
            #
            # ⚠️ When `together=False` and quantity > 1, we return individual
            # seats rather than groups. Artificially grouping them would be
            # misleading.
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
