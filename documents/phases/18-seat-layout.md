# Phase 18 — Visual Seat Layout Builder

> Previously, every event was a grid: N rows × M seats, all uniform.
> Real venues are not like that — they have aisles, sections, and rows with varying seat counts.

---

## Problem

Since Phase 10, organizers have created events using `price_tiers`:

```json
{ "seats_per_row": 10, "price_tiers": [{"rows": 2, "price": 2500}, ...] }
```

This is simple and sufficient for most events. However, it cannot handle these three requirements:

| Requirement | Why it fails |
|---|---|
| **Aisles** (walkways) in between | Seats in every row are contiguous |
| Distinct **sections** (Ground, Balcony) | Only "tier 1, tier 2" exists, no names |
| **Different seat counts** per row | `seats_per_row` is global for the event |

---

## ⭐ Decision 1 — The old path was NOT removed

The easiest path would have been to remove `price_tiers` and keep only the layout. That would be wrong:

1. **Data from 17 phases depends on it.** Seeds, tests, and demos would all break.
2. **Most events do not need a map.** Forcing a layout builder on a user for a "5 rows, 10 seats, one price" event is unnecessary friction.

So both paths exist, **but there is only one generator**:

```
layout provided      ->  map remains as-is
price_tiers provided ->  layout is GENERATED from it
                          |
                          v
                  seat_layout.expand()      <- single source of truth
                          |
                          v
                     bulk insert
```

`from_price_tiers()` converts the old input into the layout shape. Maintaining two separate generators would lead to **bugs in two places** — and they would eventually behave differently.

Benefit: Events created via `price_tiers` now store a `layout`, allowing the grid to render every event consistently.

📁 [`backend/layout.py`](../../backend/layout.py)

---

## ⭐⭐ Decision 2 — Handling legacy events

This is the most critical part of this phase.

`Event.layout` and `Seat.section` are both **nullable**:

```python
layout: Mapped[dict | None] = mapped_column(JSON, nullable=True)
section: Mapped[str | None] = mapped_column(String(40), nullable=True)
```

`NULL` signifies a "legacy uniform event" — and the frontend renders it exactly as it did before:

```js
function aisleMap(layout) {
  const map = new Map()
  if (!layout?.sections) return map     // <- legacy event, empty Map
  ...
}
```

Section headings are only displayed if there is **more than one** section. Showing "Ground" for a single-section event is just noise.

> Test `test_old_events_without_a_layout_still_work` targets this specifically: the seed event 1, `layout: null`, 100 seats, each seat `section: null` — with all other fields remaining as they were.

This is the area most prone to breaking when adding new columns, and such breakages are often discovered too late.

---

## Layout shape

```json
{
  "sections": [
    {
      "name": "Ground",
      "price": 2500,
      "rows": [
        {"label": "A", "seats": 8,  "aisles_after": [4]},
        {"label": "B", "seats": 10, "aisles_after": [3, 7]}
      ]
    },
    { "name": "Balcony", "price": 900, "rows": [{"label": "C", "seats": 12}] }
  ]
}
```

### Aisles are purely VISUAL

`aisles_after: [4]` means a gap **after** seat 4.

**No seat is created. Numbering does not stop.** This is not in the `seats` table — only in the layout JSON, as it is purely for presentation.

A common mistake is to create a "blank seat" for an aisle or to skip numbering. Both are wrong — an attendee requesting "seat 5" should not be given seat 6.

The test locks this behavior:

```python
with_aisle = expand(... _row("A", 6, [3]))
without    = expand(... _row("A", 6))
assert len(with_aisle) == len(without) == 6
assert [p.seat_number for p in with_aisle] == [1, 2, 3, 4, 5, 6]
```

---

## Validation — BEFORE creating seats

```
Pydantic (schemas.py)  ->  shape: types, lengths, ranges
layout.py              ->  business rules: duplicate labels, seat cap, aisle position
```

This separation is intentional. Shape rules are easy to write in a schema, but rules like "the same row label cannot exist in two sections" require **context of the entire layout** — and they should be testable without a database.

### ⭐ Most important rule: row label must be unique across the event

```python
if label in seen_labels:
    raise LayoutError(f"Row '{label}' exists in two places — ...")
```

`seats` has a `UNIQUE(event_id, row_label, seat_number)` constraint (from Phase 2). Without this check, expansion would fail with an `IntegrityError` **after inserting 500 seats**.

The constraint is unaware of sections — hence, the label must be unique across the entire event, not just within a section.

### expand() does not touch the DB

```python
def expand(layout: dict) -> list[PlannedSeat]:
    validate(layout)
    ...
    return seats        # list only, no inserts
```

The caller performs a bulk insert within a transaction. If `expand` performed the writes itself, a partial failure ("half the seats created") would be possible.

Test `test_bad_layout_creates_no_event` verifies this: after an invalid layout, the organizer's event count must remain unchanged.

---

## Frontend

### The builder is a FORM, not a drag-and-drop canvas

This might seem counter-intuitive, but it is a deliberate decision:

> Real venues are built in rows and sections. Typing "12 seats in Row C, aisle after seat 4" is faster and more error-proof than dragging 12 boxes with a mouse.

Drag-and-drop introduces a mountain of complexity: pointer-events, undo/redo, snapping, and touch handling — for a feature used only a few times a year.

Instead, there is a **live preview**: the exact shape the attendee will see. This is the real purpose of the builder — it is difficult to visualize 40 seats and 4 aisles as numbers, but it is immediately clear when viewed.

📁 [`frontend/src/components/LayoutBuilder.jsx`](../../frontend/src/components/LayoutBuilder.jsx)

### Client-side validation is duplicated, and that is fine

`validateLayout()` is a copy of the server's rules. The duplication is intentional:

- These rules **must exist** on the server — any API can be hit directly, and the builder is just a UI convenience.
- However, the user should know if two rows have the same label **before** clicking submit. A round-trip is unnecessary.

The server makes the final decision; the client only provides fast feedback.

### A small detail that becomes a bug

```js
r.aisles_after = e.target.value
  .split(',')
  .map((x) => parseInt(x.trim(), 10))
  .filter((x) => Number.isInteger(x) && x > 0)
```

Without `filter`: if a user types "4," (intending to type more), `parseInt("")` returns `NaN`, causing validation errors to flash on every keystroke.

### Sections and aisles in the grid

An aisle is an empty `<span>` — no seat, no interaction:

```jsx
{gaps?.has(seat.seat_number) && (
  <span className="w-4 shrink-0" aria-hidden="true" />
)}
```

`aria-hidden` is used because this element does not exist for screen readers.

---

## Proof

```
=== A. From LAYOUT ===
201 30 seats
 sections: Counter({'Ground': 18, 'Balcony': 12})
 A row prices: {2500.0}
 C row prices: {900.0}
 layout stored: True | aisles A: [4]

=== B. Legacy price_tiers path (backwards compat) ===
201 12 seats
 sections: Counter({'Tier 2': 8, 'Tier 1': 4})
 layout auto-generated: {"sections": [{"name": "Tier 1", "price": 1500.0, ...

=== C. Invalid layouts must be rejected ===
  duplicate row label      -> 422  Row 'A' exists in two places — row labels must be unique across the event
  aisle outside row        -> 422  Row 'A': aisle position 9 must be within row (1-4)
  duplicate section name   -> 422  Two sections have the same name: X

=== D. Legacy events (layout NULL) still work ===
 event 1: layout=None, 100 seats, section=None
```

### Tests

**90/90 passed** (79 previous + 11 new).

*Pure functions (no DB):*
- `test_expand_produces_every_seat`
- ⭐ `test_aisles_do_not_create_or_skip_seats`
- ⭐ `test_duplicate_row_label_across_sections_is_rejected`
- `test_aisle_outside_row_is_rejected`
- `test_duplicate_section_name_is_rejected`
- `test_empty_and_oversized_layouts_are_rejected`
- `test_price_tiers_convert_to_the_same_shape`

*HTTP flow:*
- `test_create_event_from_layout`
- ⭐ `test_bad_layout_creates_no_event`
- `test_price_tiers_path_still_works_and_stores_a_layout`
- ⭐⭐ `test_old_events_without_a_layout_still_work`

```bash
docker compose exec backend python -m pytest tests/ -q -k "layout or aisle"
```

---

## What broke

No major bugs were found in this phase — which is worth noting.

The reason is likely that `layout.py` consists of **pure functions**. No DB, no network, no state. Testing such code is so inexpensive that errors are caught while writing, before execution.

Two minor things occurred:

**Migration did not get stuck on `NOT NULL`.** Both new columns are nullable, so the old pattern ([Phase 14](14-dynamic-pricing.md), 16) was not required. Keeping them nullable was not just for backwards compatibility — it also simplified the migration.

**`layout` name shadowing.** The module is named `layout` and the route required a local variable named `layout`. Cleared by `import layout as seat_layout` — otherwise, this would have been a difficult bug to track down later.

---

## What was intentionally NOT built

- **Layout editing.** Changing a map after an event is created means changing seats, which may already have bookings. The same principle as base pricing applies ([Phase 14](14-dynamic-pricing.md)): do not change references to items already sold.
- **Curved / angled rows.** Rows in stadiums are not straight. That would require x/y coordinates for every seat — an entirely different data model.
- **Templates.** "Save this layout to use in the next event" — now that layout JSON is stored, adding this will be easy.
- **Blocked/broken seats.** In real venues, some seats are not for sale (behind pillars, camera positions). That requires an additional seat status.

---

## Files

**New:**
| File | Purpose |
|---|---|
| `backend/layout.py` | Validation + expansion, pure functions |
| `frontend/src/components/LayoutBuilder.jsx` | Builder + live preview |

**Modified:**
| File | Purpose |
|---|---|
| `backend/models.py` | `Event.layout` (JSON), `Seat.section` — both nullable |
| `backend/schemas.py` | `SeatLayout`, `LayoutSection`, `LayoutRow`; `EventCreate.layout` optional |
| `backend/routers/organizer.py` | Both paths use the same expansion |
| `backend/routers/events.py` | `layout` in detail |
| `backend/routers/seats.py` | `SeatOut.section` |
| `frontend/src/components/SeatGrid.jsx` | Render sections + aisles |
| `frontend/src/pages/organizer/CreateEvent.jsx` | Simple / Layout builder toggle |

---

## Related

- [Phase 02 — Postgres + Models](02-postgres-models.md) — the unique constraint that necessitates validation
- [Phase 10 — RBAC + Organizer](10-rbac-organizer.md) — the legacy `price_tiers` path
- [Phase 14 — Dynamic Pricing](14-dynamic-pricing.md) — "do not change references to items already sold"
- [testing.md](../reference/testing.md) — commands
