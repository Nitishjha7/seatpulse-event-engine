"""
Seat layout — defines venue structure and seat generation logic.

Events could already be defined with `price_tiers` ("2 rows @ ₹2500, 3 rows
@ ₹1200", uniform seats per row), which is simple and covers most events.
Real venues are messier though: aisles (walkways), distinct sections
(Ground, Balcony) with their own pricing and names, and row capacities that
vary (fewer seats up front, more in back). This file describes that layout
and generates the seat entities from it.

`price_tiers` stays supported alongside it — existing event seeds, tests,
and demos depend on it, and most events don't need a full layout builder;
"5 rows, 10 seats, one price" shouldn't require one. Both paths produce the
same seat entities; layout-builder events just also populate `Event.layout`
so the grid can render aisles and sections.

Layout schema:

    {
      "sections": [
        {
          "name": "Ground",
          "price": 2500,
          "rows": [
            {"label": "A", "seats": 10, "aisles_after": [4]},
            {"label": "B", "seats": 12}
          ]
        }
      ]
    }

`aisles_after: [4]` is a visual gap after seat 4 only — no seat is created
and no number is skipped. Stored in the layout JSON, not the database.
"""

from dataclasses import dataclass

# Limits must match `price_tiers` to ensure consistent behavior across both methods.
MAX_SEATS_PER_EVENT = 2000
MAX_SECTIONS = 10
MAX_ROWS_PER_SECTION = 40
MAX_SEATS_PER_ROW = 60
MAX_LABEL_LEN = 4


class LayoutError(ValueError):
    """Raised when a layout is invalid; mapped to 422 by the router."""


@dataclass(frozen=True)
class PlannedSeat:
    section: str
    row_label: str
    seat_number: int
    price: float


def validate(layout: dict) -> None:
    """
    Validates the layout structure before seat generation, server-side
    regardless of frontend validation — the layout builder is a UI
    convenience, not a substitute for guarding the API against bad input.
    Kept separate from `expand()` so it's testable without a database and
    catches errors before any seats get created.
    """
    sections = layout.get("sections")
    if not isinstance(sections, list) or not sections:
        raise LayoutError("At least one section is required")

    if len(sections) > MAX_SECTIONS:
        raise LayoutError(f"Max {MAX_SECTIONS} sections")

    seen_labels: set[str] = set()
    seen_sections: set[str] = set()
    total = 0

    for i, section in enumerate(sections):
        name = str(section.get("name", "")).strip()
        if not name:
            raise LayoutError(f"Section {i + 1} has an empty name")
        if len(name) > 40:
            raise LayoutError(f"Section name is too long: {name[:20]}…")
        if name in seen_sections:
            raise LayoutError(f"Two sections share the same name: {name}")
        seen_sections.add(name)

        price = section.get("price")
        if not isinstance(price, (int, float)) or price < 0 or price > 1_000_000:
            raise LayoutError(f"'{name}' has an invalid price")

        rows = section.get("rows")
        if not isinstance(rows, list) or not rows:
            raise LayoutError(f"'{name}' needs at least one row")
        if len(rows) > MAX_ROWS_PER_SECTION:
            raise LayoutError(f"'{name}' allows at most {MAX_ROWS_PER_SECTION} rows")

        for row in rows:
            label = str(row.get("label", "")).strip().upper()
            if not label:
                raise LayoutError(f"'{name}' has a row with an empty label")
            if len(label) > MAX_LABEL_LEN:
                raise LayoutError(f"Row label is too long: {label}")

            # The `seats` table enforces UNIQUE(event_id, row_label,
            # seat_number), so duplicate row labels across sections would
            # hit an IntegrityError mid-insert. Catch it here instead —
            # labels must be unique across the whole event, not per section.
            if label in seen_labels:
                raise LayoutError(
                    f"Row '{label}' appears twice — every row label must be unique across the event"
                )
            seen_labels.add(label)

            count = row.get("seats")
            if not isinstance(count, int) or count < 1 or count > MAX_SEATS_PER_ROW:
                raise LayoutError(f"Row '{label}' must have between 1 and {MAX_SEATS_PER_ROW} seats")

            aisles = row.get("aisles_after", [])
            if not isinstance(aisles, list):
                raise LayoutError(f"Row '{label}': aisles_after must be a list")
            for a in aisles:
                if not isinstance(a, int) or a < 1 or a >= count:
                    raise LayoutError(
                        f"Row '{label}': aisle position {a} must fall inside the row (1-{count - 1})"
                    )

            total += count

    if total > MAX_SEATS_PER_EVENT:
        raise LayoutError(f"At most {MAX_SEATS_PER_EVENT} seats — this layout has {total}")


def expand(layout: dict) -> list[PlannedSeat]:
    """
    Generates a list of seats from the layout. Doesn't touch the database —
    returns plain objects; the caller does the bulk insert inside a
    transaction.
    """
    validate(layout)

    seats: list[PlannedSeat] = []
    for section in layout["sections"]:
        name = str(section["name"]).strip()
        price = float(section["price"])
        for row in section["rows"]:
            label = str(row["label"]).strip().upper()
            for n in range(1, int(row["seats"]) + 1):
                seats.append(PlannedSeat(name, label, n, price))
    return seats


def summarise(layout: dict) -> dict:
    """Summarizes the layout without generating seats — used for live UI previews and validation feedback."""
    try:
        validate(layout)
    except LayoutError as exc:
        return {"valid": False, "error": str(exc)}

    prices = [float(s["price"]) for s in layout["sections"]]
    total = sum(
        int(r["seats"]) for s in layout["sections"] for r in s["rows"]
    )

    return {
        "valid": True,
        "sections": len(layout["sections"]),
        "rows": sum(len(s["rows"]) for s in layout["sections"]),
        "total_seats": total,
        "min_price": min(prices),
        "max_price": max(prices),
    }


def from_price_tiers(tiers: list[dict], seats_per_row: int, row_labels: str) -> dict:
    """
    Converts legacy `price_tiers` to the layout schema so both paths share
    the same expansion logic and legacy events can use grid visualization.
    Each tier becomes a section with an auto-generated name.
    """
    sections = []
    row_index = 0
    for i, tier in enumerate(tiers, start=1):
        rows = []
        for _ in range(int(tier["rows"])):
            rows.append({"label": row_labels[row_index], "seats": seats_per_row})
            row_index += 1
        sections.append({
            "name": f"Tier {i}" if len(tiers) > 1 else "General",
            "price": float(tier["price"]),
            "rows": rows,
        })
    return {"sections": sections}
