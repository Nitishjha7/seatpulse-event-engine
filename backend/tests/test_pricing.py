"""
Dynamic (surge) pricing: the formula and the quoted-price promise.
"""

import pytest

from helpers import auth_headers
from pricing import apply, multiplier_for, pricing_for_event


# ---------------------------------------------------------------------------
# Phase 14 — Dynamic pricing
#
# Two separate things are being tested here:
#   1. The FORMULA is correct (pure functions, no DB).
#   2. The QUOTED PRICE promise is kept (full HTTP flow).
#
# (2) is more important. If the formula is wrong, the price might look odd.
# If the price lock breaks, the user will be charged incorrectly — that is a different level of bug.
# ---------------------------------------------------------------------------

def test_multiplier_grows_with_demand():
    """0% sold = base, 100% sold = base x (1 + demand_factor)."""
    assert multiplier_for(0, 100, 0.5, 2.0) == 1.0
    assert multiplier_for(50, 100, 0.5, 2.0) == 1.25
    assert multiplier_for(100, 100, 0.5, 2.0) == 1.5


def test_max_surge_is_a_hard_ceiling():
    """Regardless of the demand_factor, it cannot exceed the max_surge."""
    # 100% sold, factor 5.0 -> formula says 6.0, cap is 1.5
    assert multiplier_for(100, 100, 5.0, 1.5) == 1.5


def test_empty_event_does_not_divide_by_zero():
    """Should not crash when total=0 — this happens when creating a new event."""
    assert multiplier_for(0, 0, 0.5, 2.0) == 1.0


def test_price_rounds_to_a_clean_number():
    """Not ₹827.43, but ₹830. Users get suspicious of odd prices."""
    assert apply(827.43, 1.0) == 830.0
    assert apply(1000, 1.25) == 1250.0


def test_disabled_pricing_never_surges():
    """
    When disabled, the multiplier is always 1.0 — even if the event is sold out.

    This is the default, and it will apply to most events.
    """
    info = pricing_for_event(
        enabled=False, sold=100, total=100, demand_factor=0.5, max_surge=2.0
    )
    assert info.multiplier == 1.0
    assert info.seats_until_increase is None


def test_seats_until_increase_counts_forward():
    """
    100 seats, factor 0.5, base ₹1000:
      0 sold -> 1.000x -> ₹1000
      1 sold -> 1.005x -> ₹1005 -> round to ₹10 -> ₹1000 (no change)
      2 sold -> 1.010x -> ₹1010                            <- change here

    So the answer is 2, not 1. It seems like 1 at first glance — but the ₹5 difference disappears due to the ₹10 rounding. That is why this function runs a loop instead of estimating with a formula.
    """
    info = pricing_for_event(
        enabled=True, sold=0, total=100, demand_factor=0.5, max_surge=2.0,
        sample_base=1000.0,
    )
    assert info.seats_until_increase == 2

    # Small factor -> price increases slowly -> more seats required
    slow = pricing_for_event(
        enabled=True, sold=0, total=100, demand_factor=0.1, max_surge=2.0,
        sample_base=1000.0,
    )
    assert slow.seats_until_increase > 1


def test_max_surge_reached_reports_no_further_increase():
    """Don't lie about 'further increases' once the cap is reached."""
    info = pricing_for_event(
        enabled=True, sold=50, total=100, demand_factor=5.0, max_surge=1.0,
        sample_base=1000.0,
    )
    assert info.multiplier == 1.0
    assert info.seats_until_increase is None


# ---- Now the HTTP flow — the real promise is tested here ----

# Cleanup for surge_event fixture bookings — which tokens purchased something
# for this event. Module-level so the fixture can know about tokens created inside the test.
tokens_cache: list[str] = []


@pytest.fixture
def surge_event(client, role_tokens):
    """
    A small event with dynamic pricing, its own.

    Cannot use Event 1 — other tests keep creating/deleting bookings on it, and the multiplier comes from the sold-count. On a shared event, this test would sometimes pass and sometimes fail (flaky), and a flaky test is a bad test.
    """
    token = role_tokens["organizer"]
    res = client.post(
        "/api/organizer/events",
        headers=auth_headers(token),
        json={
            "name": "Surge Test Event",
            "venue": "Test Hall, Pune",
            "starts_at": "2027-06-01T18:00:00Z",
            "seats_per_row": 5,
            "price_tiers": [{"rows": 2, "price": 1000}],   # 10 seats @ ₹1000
            "dynamic_pricing": True,
            # 10 seats, factor 1.0 -> +10% per booking — effect is clearly visible
            "demand_factor": 1.0,
            "max_surge": 2.0,
        },
    )
    assert res.status_code == 201
    event = res.json()

    yield event

    # Cleanup: remove bookings, then the event (events with bookings cannot be deleted).
    for t in [role_tokens["organizer"], role_tokens["admin"]] + tokens_cache:
        for b in client.get("/api/bookings", headers=auth_headers(t)).json():
            if b["event_id"] == event["id"] and b["status"] == "confirmed":
                client.delete(f"/api/bookings/{b['id']}", headers=auth_headers(t))
    tokens_cache.clear()
    client.delete(f"/api/organizer/events/{event['id']}", headers=auth_headers(token))


def test_new_event_starts_at_base_price(client, surge_event):
    seats = client.get(f"/api/events/{surge_event['id']}/seats").json()
    assert all(s["current_price"] == s["price"] == 1000.0 for s in seats)

    detail = client.get(f"/api/events/{surge_event['id']}").json()
    assert detail["pricing"]["enabled"] is True
    assert detail["pricing"]["multiplier"] == 1.0
    assert detail["pricing"]["surge_percent"] == 0


def test_price_rises_after_a_booking(client, tokens, surge_event):
    """The remaining seats should increase in price as soon as one is sold."""
    tokens_cache.append(tokens[0])
    seats = client.get(f"/api/events/{surge_event['id']}/seats").json()

    res = client.post(
        "/api/bookings",
        headers=auth_headers(tokens[0]),
        json={"seat_id": seats[0]["id"]},
    )
    assert res.status_code == 201

    after = client.get(f"/api/events/{surge_event['id']}/seats").json()
    unsold = [s for s in after if s["status"] == "available"]

    # 1/10 sold, factor 1.0 -> 1.1x -> ₹1100
    assert all(s["current_price"] == 1100.0 for s in unsold)
    # BASE price did not change — this is the foundation of the entire design
    assert all(s["price"] == 1000.0 for s in unsold)


def test_held_price_survives_a_price_rise(client, tokens, surge_event):
    """
    ⭐ The most critical test for this feature.

    User A holds a seat (quoted ₹1000). Then User B buys another seat, increasing demand.
    A must still pay ₹1000 — because that was the quoted price.

    If this breaks, the user will be silently overcharged.
    """
    tokens_cache.extend([tokens[0], tokens[1]])
    seats = client.get(f"/api/events/{surge_event['id']}/seats").json()
    a_seat, b_seat = seats[0], seats[1]

    # A holds the seat — quote is locked
    lock = client.post(
        f"/api/seats/{a_seat['id']}/lock", headers=auth_headers(tokens[0])
    ).json()
    quoted = lock["price"]
    assert quoted == 1000.0

    # B buys — demand increases
    assert client.post(
        "/api/bookings", headers=auth_headers(tokens[1]), json={"seat_id": b_seat["id"]}
    ).status_code == 201

    # Price increased for everyone else...
    fresh = client.get(f"/api/events/{surge_event['id']}/seats").json()
    others = [s for s in fresh if s["status"] == "available"]
    assert others and all(s["current_price"] > 1000.0 for s in others)

    # ...but A's hold is still at ₹1000
    held = next(s for s in fresh if s["id"] == a_seat["id"])
    assert held["held_price"] == 1000.0

    # And exactly the same amount was charged in the booking
    booking = client.post(
        "/api/bookings", headers=auth_headers(tokens[0]), json={"seat_id": a_seat["id"]}
    )
    assert booking.status_code == 201
    assert booking.json()["amount"] == quoted


def test_releasing_a_hold_drops_the_locked_price(client, tokens, surge_event):
    """
    Releasing a hold removes the locked price.

    Without this, a user could hold-release-hold to keep the lowest price
    indefinitely — rendering surge pricing meaningless.
    """
    seats = client.get(f"/api/events/{surge_event['id']}/seats").json()
    seat = [s for s in seats if s["status"] == "available"][-1]

    client.post(f"/api/seats/{seat['id']}/lock", headers=auth_headers(tokens[0]))
    client.delete(f"/api/seats/{seat['id']}/lock", headers=auth_headers(tokens[0]))

    fresh = client.get(f"/api/seats/{seat['id']}").json()
    assert fresh["held_price"] is None


def test_organizer_can_turn_surge_off(client, role_tokens, surge_event):
    """Allow organizer to disable surge if sales are slow — revert to base price."""
    res = client.patch(
        f"/api/organizer/events/{surge_event['id']}",
        headers=auth_headers(role_tokens["organizer"]),
        json={"dynamic_pricing": False},
    )
    assert res.status_code == 200

    detail = client.get(f"/api/events/{surge_event['id']}").json()
    assert detail["pricing"]["enabled"] is False

    seats = client.get(f"/api/events/{surge_event['id']}/seats").json()
    assert all(s["current_price"] == s["price"] for s in seats)


def test_base_price_cannot_be_edited(client, role_tokens, surge_event):
    """
    Base price cannot be changed via PATCH.

    Existing bookings rely on it — changing it would invalidate their receipts.
    Pydantic silently ignores extra fields, so we verify that no change occurred.
    """
    client.patch(
        f"/api/organizer/events/{surge_event['id']}",
        headers=auth_headers(role_tokens["organizer"]),
        json={"price_tiers": [{"rows": 2, "price": 99999}]},
    )
    seats = client.get(f"/api/events/{surge_event['id']}/seats").json()
    assert all(s["price"] == 1000.0 for s in seats)


def test_absurd_surge_settings_are_rejected(client, role_tokens, surge_event):
    """Server should reject accidental typos like demand_factor=50."""
    res = client.patch(
        f"/api/organizer/events/{surge_event['id']}",
        headers=auth_headers(role_tokens["organizer"]),
        json={"demand_factor": 50},
    )
    assert res.status_code == 422
