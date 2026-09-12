"""
Seat layouts: validation and expansion as pure functions, plus event
creation through both the layout and the price-tier paths.
"""

import pytest

import layout as seat_layout
from helpers import auth_headers


# ---------------------------------------------------------------------------
# Phase 18 — Seat layout
#
# Two parts:
#   1. validate/expand — pure functions, no DB access.
#   2. HTTP flow — both paths (layout and price_tiers) lead to the same destination.
#
# Most important invariant: **existing events (layout NULL) must not break.**
# ---------------------------------------------------------------------------

def _layout(*sections):
    return {"sections": list(sections)}


def _section(name, price, *rows):
    return {"name": name, "price": price, "rows": list(rows)}


def _row(label, seats, aisles=None):
    return {"label": label, "seats": seats, "aisles_after": aisles or []}


def test_expand_produces_every_seat():
    plan = seat_layout.expand(
        _layout(
            _section("Ground", 2500, _row("A", 3), _row("B", 2)),
            _section("Balcony", 800, _row("C", 4)),
        )
    )
    assert len(plan) == 9
    assert {p.section for p in plan} == {"Ground", "Balcony"}
    # Price comes from the section, not the row.
    assert {p.price for p in plan if p.section == "Balcony"} == {800.0}
    # Numbering starts at 1 in every row.
    assert sorted(p.seat_number for p in plan if p.row_label == "A") == [1, 2, 3]


def test_aisles_do_not_create_or_skip_seats():
    """
    ⭐ Aisles are purely visual.

    A common mistake is treating an aisle as an "empty seat" or skipping numbering after it. Both are wrong — an attendee requesting "seat 5" should not receive seat 6.
    """
    with_aisle = seat_layout.expand(_layout(_section("X", 100, _row("A", 6, [3]))))
    without = seat_layout.expand(_layout(_section("X", 100, _row("A", 6))))

    assert len(with_aisle) == len(without) == 6
    assert [p.seat_number for p in with_aisle] == [1, 2, 3, 4, 5, 6]


def test_duplicate_row_label_across_sections_is_rejected():
    """
    ⭐ `seats` has a UNIQUE(event_id, row_label, seat_number) constraint.

    Without catching this, expansion would fail with an IntegrityError AFTER inserting 500 seats — by which time the transaction would be heavy.
    """
    with pytest.raises(seat_layout.LayoutError, match="appears twice"):
        seat_layout.validate(
            _layout(
                _section("Ground", 100, _row("A", 5)),
                _section("Balcony", 200, _row("A", 5)),
            )
        )


def test_aisle_outside_row_is_rejected():
    """An aisle after the last seat is meaningless — it is the end of the row."""
    with pytest.raises(seat_layout.LayoutError, match="aisle position"):
        seat_layout.validate(_layout(_section("X", 100, _row("A", 5, [5]))))

    with pytest.raises(seat_layout.LayoutError, match="aisle position"):
        seat_layout.validate(_layout(_section("X", 100, _row("A", 5, [9]))))

    # 4 is valid — in 5 seats, a gap can be created after seat 4.
    seat_layout.validate(_layout(_section("X", 100, _row("A", 5, [4]))))


def test_duplicate_section_name_is_rejected():
    with pytest.raises(seat_layout.LayoutError, match="share the same name"):
        seat_layout.validate(
            _layout(
                _section("Ground", 100, _row("A", 5)),
                _section("Ground", 200, _row("B", 5)),
            )
        )


def test_empty_and_oversized_layouts_are_rejected():
    with pytest.raises(seat_layout.LayoutError):
        seat_layout.validate({"sections": []})

    with pytest.raises(seat_layout.LayoutError):
        seat_layout.validate(_layout(_section("X", 100)))     # no rows

    huge = _layout(
        _section("X", 100, *[_row(f"R{i}", 60) for i in range(40)])
    )
    with pytest.raises(seat_layout.LayoutError, match="At most 2000"):
        seat_layout.validate(huge)


def test_price_tiers_convert_to_the_same_shape():
    """
    The legacy path also uses the layout generator.

    Maintaining two separate generators leads to bugs in two places — and they eventually start behaving differently.
    """
    converted = seat_layout.from_price_tiers(
        [{"rows": 1, "price": 1500}, {"rows": 2, "price": 500}],
        seats_per_row=4,
        row_labels="ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    )
    plan = seat_layout.expand(converted)

    assert len(plan) == 12                       # 3 rows x 4
    assert [p.row_label for p in plan[:4]] == ["A"] * 4
    assert {p.price for p in plan if p.row_label == "A"} == {1500.0}
    assert {p.price for p in plan if p.row_label in ("B", "C")} == {500.0}


# ---- HTTP flow ----

def test_create_event_from_layout(client, role_tokens):
    token = role_tokens["organizer"]
    res = client.post(
        "/api/organizer/events",
        headers=auth_headers(token),
        json={
            "name": "Layout Event",
            "venue": "Test Arena",
            "starts_at": "2027-12-01T18:00:00Z",
            "layout": _layout(
                _section("Ground", 2500, _row("A", 8, [4]), _row("B", 10)),
                _section("Balcony", 900, _row("C", 12)),
            ),
        },
    )
    assert res.status_code == 201, res.text
    event = res.json()
    assert event["total_seats"] == 30

    seats = client.get(f"/api/events/{event['id']}/seats").json()
    assert len(seats) == 30
    assert {s["section"] for s in seats} == {"Ground", "Balcony"}
    assert {s["price"] for s in seats if s["section"] == "Balcony"} == {900.0}

    # Layout stored — the grid uses this to show aisles.
    detail = client.get(f"/api/events/{event['id']}").json()
    assert detail["layout"]["sections"][0]["rows"][0]["aisles_after"] == [4]

    assert client.delete(
        f"/api/organizer/events/{event['id']}", headers=auth_headers(token)
    ).status_code == 204


def test_bad_layout_creates_no_event(client, role_tokens):
    """
    ⭐ No seats (or events) should be created with an invalid layout.

    Validation runs BEFORE expansion, so the DB remains untouched. A partially created event is the worst-case scenario.
    """
    token = role_tokens["organizer"]
    before = len(client.get("/api/organizer/events", headers=auth_headers(token)).json())

    res = client.post(
        "/api/organizer/events",
        headers=auth_headers(token),
        json={
            "name": "Broken Layout",
            "venue": "Test Arena",
            "starts_at": "2027-12-01T18:00:00Z",
            "layout": _layout(
                _section("A", 100, _row("X", 5)),
                _section("B", 200, _row("X", 5)),      # duplicate label
            ),
        },
    )
    assert res.status_code == 422
    assert "appears twice" in res.json()["detail"]

    after = len(client.get("/api/organizer/events", headers=auth_headers(token)).json())
    assert after == before, "event was created even though it should have failed"


def test_price_tiers_path_still_works_and_stores_a_layout(client, role_tokens):
    """
    Backwards compatibility — the legacy request body must work exactly as it did in Phase 10.
    """
    token = role_tokens["organizer"]
    res = client.post(
        "/api/organizer/events",
        headers=auth_headers(token),
        json={
            "name": "Tier Event",
            "venue": "Test Arena",
            "starts_at": "2027-12-01T18:00:00Z",
            "seats_per_row": 4,
            "price_tiers": [{"rows": 1, "price": 1500}, {"rows": 2, "price": 500}],
        },
    )
    assert res.status_code == 201, res.text
    event = res.json()
    assert event["total_seats"] == 12

    # Events created via price_tiers also store a layout.
    detail = client.get(f"/api/events/{event['id']}").json()
    assert detail["layout"] is not None
    assert len(detail["layout"]["sections"]) == 2

    seats = client.get(f"/api/events/{event['id']}/seats").json()
    assert {s["price"] for s in seats if s["row_label"] == "A"} == {1500.0}

    client.delete(f"/api/organizer/events/{event['id']}", headers=auth_headers(token))


def test_old_events_without_a_layout_still_work(client):
    """
    ⭐⭐ Most important test.

    Event 1 comes from the seed and has a NULL `layout`. 17 phases of demo data, tests, and bookings rely on it. The new column is optional, and nothing should break because of it.
    """
    detail = client.get("/api/events/1").json()
    assert detail["layout"] is None

    seats = client.get("/api/events/1/seats").json()
    assert len(seats) == 100
    assert all(s["section"] is None for s in seats)
    # All other fields remain the same.
    assert all("price" in s and "status" in s and "version" in s for s in seats)
