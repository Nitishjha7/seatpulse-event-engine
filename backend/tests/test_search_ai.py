"""
Natural-language seat search and AI event-copy drafts.

No test here needs a Gemini key. The model only turns text into filters (or
a draft); search, RBAC and validation around it are ordinary code that must
keep working with AI switched off.
"""

import seat_search
from helpers import auth_headers


# ---------------------------------------------------------------------------
# Phase 19 — Seat search
#
# ⭐ NONE of these tests require Gemini.
#
# This is intentional. The LLM only performs "text -> filters"; the entire search process thereafter is standard code. If these tests required an API key, they would be skipped in CI — and skipped tests appear green (a mistake caught in Phase 16).
# ---------------------------------------------------------------------------

class _FakeSeat:
    """A minimal seat for testing — no need to create a full ORM object."""

    def __init__(self, id, row, num, price=1000, status="available", section=None):
        self.id = id
        self.row_label = row
        self.seat_number = num
        self.price = price
        self.status = status
        self.section = section


def _seat_row(label, count, *, taken=(), price=1000, section=None, start_id=1):
    return [
        _FakeSeat(
            start_id + i,
            label,
            i + 1,
            price=price,
            status="booked" if (i + 1) in taken else "available",
            section=section,
        )
        for i in range(count)
    ]


def test_single_seat_search_returns_cheapest_first():
    seats = _seat_row("A", 3, price=2000, start_id=1) + _seat_row("B", 3, price=500, start_id=10)
    found = seat_search.find(seats, quantity=1)

    assert found[0].total_price == 500
    assert found[0].row_label == "B"


def test_together_needs_consecutive_seats():
    """If there is a booked seat in between, they are not 'together'."""
    # A: seats 1,2,[3 booked],4,5  -> no run of 3 adjacent seats exists
    seats = _seat_row("A", 5, taken=(3,))

    assert seat_search.find(seats, quantity=3, together=True) == []
    # two adjacent pairs remain (1-2 and 4-5)
    assert len(seat_search.find(seats, quantity=2, together=True)) == 2


def test_together_false_returns_individual_seats():
    """
    "I need 3 seats, not necessarily together" means "show me any 3 seats".

    Artificially grouping them would be misleading.
    """
    seats = _seat_row("A", 5, taken=(3,))
    found = seat_search.find(seats, quantity=3, together=False)

    assert len(found) == 4                        # 4 available seats
    assert all(len(m.seat_ids) == 1 for m in found)


def test_aisle_breaks_togetherness():
    """
    ⭐⭐ Phase 18 layout data is used here.

    There is an aisle between seats 2 and 3. The numbers are consecutive, but the seats are NOT together — people will be passing through.

    Without this check, the search would suggest "adjacent seats" that aren't actually together, which the user would only discover upon arriving at the venue.
    """
    seats = _seat_row("A", 6)
    layout = {
        "sections": [
            {"name": "X", "price": 1000, "rows": [{"label": "A", "seats": 6, "aisles_after": [2]}]}
        ]
    }

    # Without layout: 1-2-3, 2-3-4, 3-4-5, 4-5-6 = 4 groups
    assert len(seat_search.find(seats, quantity=3, together=True)) == 4

    # With layout: aisle is after 2, so only 3-4-5 and 4-5-6 remain
    with_layout = seat_search.find(seats, quantity=3, together=True, layout=layout)
    assert len(with_layout) == 2
    assert all(m.seat_numbers[0] >= 3 for m in with_layout)


def test_price_filters():
    seats = _seat_row("A", 2, price=500, start_id=1) + _seat_row("B", 2, price=3000, start_id=10)

    cheap = seat_search.find(seats, quantity=1, max_price=1000)
    assert {m.row_label for m in cheap} == {"A"}

    dear = seat_search.find(seats, quantity=1, min_price=1000)
    assert {m.row_label for m in dear} == {"B"}


def test_section_filter_is_case_insensitive():
    seats = (
        _seat_row("A", 2, section="Ground", start_id=1)
        + _seat_row("B", 2, section="Balcony", start_id=10)
    )
    found = seat_search.find(seats, quantity=1, section="ground")

    assert {m.section for m in found} == {"Ground"}


def test_row_preference_beats_price():
    """
    If "near the stage" is requested, don't prioritize cheaper seats further back.

    Row A is closest to the stage — this has been the convention since Phase 3.
    """
    seats = _seat_row("A", 2, price=3000, start_id=1) + _seat_row("Z", 2, price=100, start_id=10)

    front = seat_search.find(seats, quantity=1, row_preference="front")
    assert front[0].row_label == "A"

    back = seat_search.find(seats, quantity=1, row_preference="back")
    assert back[0].row_label == "Z"

    # Without preference, prioritize cheaper seats
    default = seat_search.find(seats, quantity=1)
    assert default[0].row_label == "Z"


def test_booked_seats_never_appear():
    seats = _seat_row("A", 3, taken=(1, 2, 3))
    assert seat_search.find(seats, quantity=1) == []


def test_quantity_is_clamped():
    """Regardless of model or user input, cap at 10."""
    seats = _seat_row("A", 40)
    assert seat_search.find(seats, quantity=999, together=True) != []


# ---- HTTP flow (without AI) ----

def test_search_endpoint_works_without_ai(client, tokens):
    """
    ⭐ Search must work with filters even without AI.

    This is the most critical invariant of the feature: AI is an addition, not a dependency. If the key is missing, the model is down, or the quota is exhausted — search must still function.
    """
    res = client.post(
        "/api/events/1/seats/search",
        headers=auth_headers(tokens[0]),
        json={"filters": {"quantity": 2, "together": True, "max_price": 999999}},
    )
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["filters"]["quantity"] == 2
    assert body["interpreted"] is False        # AI was never used
    assert len(body["matches"]) > 0
    assert all(len(m["seat_ids"]) == 2 for m in body["matches"])


def test_search_respects_max_price(client, tokens):
    seats = client.get("/api/events/1/seats").json()
    cheapest = min(s["current_price"] or s["price"] for s in seats)

    res = client.post(
        "/api/events/1/seats/search",
        headers=auth_headers(tokens[0]),
        json={"filters": {"quantity": 1, "max_price": cheapest}},
    )
    assert res.status_code == 200
    for match in res.json()["matches"]:
        assert match["total_price"] <= cheapest


def test_search_needs_auth(client):
    """
    Login is required — not because the data is private (seats are public), but because rate limits are per-user and AI call costs must be attributed to a specific user.
    """
    res = client.post("/api/events/1/seats/search", json={"filters": {"quantity": 1}})
    assert res.status_code == 401


def test_search_on_unknown_event_is_404(client, tokens):
    res = client.post(
        "/api/events/999999/seats/search",
        headers=auth_headers(tokens[0]),
        json={"filters": {"quantity": 1}},
    )
    assert res.status_code == 404


def test_absurd_filters_are_rejected(client, tokens):
    """
    ⭐ This is a security boundary test.

    `SeatFilters` is where LLM output is validated. If it allows garbage values, the model (or any caller) could create an unbounded query.
    """
    res = client.post(
        "/api/events/1/seats/search",
        headers=auth_headers(tokens[0]),
        json={"filters": {"quantity": 9999}},
    )
    assert res.status_code == 422

    res = client.post(
        "/api/events/1/seats/search",
        headers=auth_headers(tokens[0]),
        json={"filters": {"quantity": 1, "row_preference": "sideways"}},
    )
    assert res.status_code == 422


def test_config_exposes_ai_flag(client):
    """The frontend uses this to decide whether to show the search box."""
    body = client.get("/api/auth/config").json()
    assert "ai_search_enabled" in body
    assert isinstance(body["ai_search_enabled"], bool)


# ---------------------------------------------------------------------------
# Phase 20 — AI event copy
#
# These tests do not require an API key. The things being tested —
# RBAC, validation, and "clean 503 if AI is off" — must hold true even without AI.
#
# AI OUTPUT cannot be tested (the model writes differently every time, as it should).
# Therefore, we test the surrounding contract here, not the internal content.
# ---------------------------------------------------------------------------

def test_draft_needs_organizer_role(client, tokens, role_tokens):
    """An attendee cannot create an event, so they cannot request a draft."""
    res = client.post(
        "/api/organizer/events/draft",
        headers=auth_headers(role_tokens["attendee"]),
        json={"brief": "some music event in Mumbai"},
    )
    assert res.status_code == 403


def test_draft_needs_auth(client):
    res = client.post(
        "/api/organizer/events/draft", json={"brief": "some music event in Mumbai"}
    )
    assert res.status_code == 401


def test_draft_rejects_empty_or_huge_briefs(client, role_tokens):
    token = role_tokens["organizer"]

    assert client.post(
        "/api/organizer/events/draft", headers=auth_headers(token), json={"brief": "hi"}
    ).status_code == 422

    assert client.post(
        "/api/organizer/events/draft",
        headers=auth_headers(token),
        json={"brief": "x" * 500},
    ).status_code == 422


def test_draft_returns_the_three_form_fields(client, role_tokens):
    """
    The draft should return the same three fields that the form populates.

    ⚠️ We do NOT check content — the model will write differently every time, as it should. We test the contract, not the prose.

    If AI is off, we get a 503, which is a valid outcome — this test ensures the endpoint returns the correct shape OR explicitly denies the request, never a 500.
    """
    res = client.post(
        "/api/organizer/events/draft",
        headers=auth_headers(role_tokens["organizer"]),
        json={"brief": "Arijit Singh concert, DY Patil Mumbai, December"},
    )

    assert res.status_code in (200, 502, 503), res.text

    if res.status_code == 200:
        body = res.json()
        assert set(body) == {"name", "description", "category"}
        assert body["category"] in {"Music", "Comedy", "Sports", "Theatre", "Conference"}
        assert body["name"].strip()
        assert body["description"].strip()


def test_draft_does_not_create_an_event(client, role_tokens):
    """
    ⭐ Most important test.

    AI draft does not SAVE anything. The organizer sees it in the form and publishes it themselves after editing.

    The event description is a promise made to the attendee — it must be human-verified. We do not allow AI to reach the publish button.
    """
    token = role_tokens["organizer"]
    before = len(client.get("/api/organizer/events", headers=auth_headers(token)).json())

    client.post(
        "/api/organizer/events/draft",
        headers=auth_headers(token),
        json={"brief": "Some test event at a test venue in December"},
    )

    after = len(client.get("/api/organizer/events", headers=auth_headers(token)).json())
    assert after == before, "draft created an event — this should never happen"
