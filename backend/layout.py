"""
Seat layout — defines venue structure and seat generation logic.

---- Background ----

Since Phase 10, organizers have used `price_tiers` to define events: "2 rows @ ₹2500, 3 rows @ ₹1200", with uniform seats per row. This is simple and sufficient for most events.

However, real venues are more complex:
  - They have AISLEs (walkways).
  - They have distinct SECTIONS (e.g., Ground, Balcony) with unique pricing and names.
  - Row capacities vary (fewer seats in front, more in back).

This file handles the description of these layouts and the generation of seat entities.

---- Legacy Support ----

`price_tiers` remains supported because:

  1. Data from 17 phases relies on it. Breaking this would invalidate existing event seeds, tests, and demos.
  2. Most events do not require a complex layout builder. For simple "5 rows, 10 seats, one price" events, a full layout definition is unnecessary overhead.

Both methods result in the same seat entities. Events using the layout builder simply populate `Event.layout` to enable grid visualization of aisles and sections.

---- Layout Schema ----

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

`aisles_after: [4]` indicates a visual gap after seat 4. This is purely for presentation; no seat is created, and no seat number is skipped. It is stored in the layout JSON, not the database.
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
    Validates the layout structure before seat generation.

    ⚠️ This runs server-side regardless of frontend validation. The layout
    builder is a UI convenience; the API must remain protected against
    malformed input.

    Validation is decoupled to allow testing without a database and to
    identify errors before partial seat expansion occurs.
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

            # ⭐ Critical uniqueness check.
            #
            # The `seats` table enforces UNIQUE(event_id, row_label, seat_number).
            # Duplicate row labels across sections would trigger an IntegrityError
            # after partial insertion. Validating here prevents this.
            #
            # Note: Labels must be unique across the entire event, not just
            # within a section, due to database constraints.
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
                # Aisle positions must be within the row range.
                if not isinstance(a, int) or a < 1 or a >= count:
                    raise LayoutError(
                        f"Row '{label}': aisle position {a} must fall inside the row (1-{count - 1})"
                    )

            total += count

    if total > MAX_SEATS_PER_EVENT:
        raise LayoutError(f"At most {MAX_SEATS_PER_EVENT} seats — this layout has {total}")


def expand(layout: dict) -> list[PlannedSeat]:
    """
    Generates a list of seats from the layout.

    ⚠️ This does not write to the database; it returns a list of objects.

    The caller is responsible for bulk insertion within a transaction to
    ensure atomicity.
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
    """
    Provides a summary of the layout without generating seats.

    Used for live UI previews and validation feedback.
    """
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
    Converts legacy `price_tiers` to the layout schema.

    Benefits:
      - Unifies expansion logic.
      - Allows legacy events to utilize grid visualization.

    Each tier is mapped to a section with auto-generated names.
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
