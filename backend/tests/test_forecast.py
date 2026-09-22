"""
Sellout projection: the pure extrapolation math, and the HTTP endpoint
built on it.
"""

from datetime import datetime, timedelta, timezone

import pytest

from helpers import auth_headers
from services.forecast import MIN_BOOKINGS, estimate_sellout


# ---------------------------------------------------------------------------
# Pure function — no DB needed
# ---------------------------------------------------------------------------

NOW = datetime(2026, 1, 20, tzinfo=timezone.utc)
EVENT_START = datetime(2026, 2, 1, tzinfo=timezone.utc)


def test_not_enough_bookings_returns_none():
    times = [NOW - timedelta(hours=i) for i in range(MIN_BOOKINGS - 1)]
    assert estimate_sellout(
        times, total_seats=100, booked_seats=len(times),
        event_starts_at=EVENT_START, now=NOW,
    ) is None


def test_slow_pace_projects_percent_sold_by_event_date():
    """2.5/day, 12 days left, 10 already booked -> 10 + 30 = 40 of 100."""
    times = [NOW - timedelta(days=i * 0.5) for i in range(10)]
    f = estimate_sellout(
        times, total_seats=100, booked_seats=10,
        event_starts_at=EVENT_START, now=NOW,
    )
    assert f.sellout_date is None
    assert f.projected_percent_sold == 40.0


def test_fast_pace_projects_a_sellout_date_before_the_event():
    times = [NOW - timedelta(hours=i) for i in range(50)]     # 50 in the last ~2 days
    f = estimate_sellout(
        times, total_seats=100, booked_seats=50,
        event_starts_at=EVENT_START, now=NOW,
    )
    assert f.sellout_date is not None
    assert NOW < f.sellout_date < EVENT_START
    assert f.projected_percent_sold is None


def test_already_sold_out():
    times = [NOW - timedelta(days=i) for i in range(MIN_BOOKINGS)]
    f = estimate_sellout(
        times, total_seats=5, booked_seats=5,
        event_starts_at=EVENT_START, now=NOW,
    )
    assert f.sellout_date == NOW
    assert f.projected_percent_sold == 100.0


def test_zero_seats_is_handled_without_dividing_by_zero():
    times = [NOW - timedelta(hours=i) for i in range(MIN_BOOKINGS)]
    assert estimate_sellout(
        times, total_seats=0, booked_seats=0,
        event_starts_at=EVENT_START, now=NOW,
    ) is None


# ---------------------------------------------------------------------------
# HTTP flow
# ---------------------------------------------------------------------------

@pytest.fixture
def forecast_event(client, role_tokens):
    """A small event to book seats on and backdate for the forecast tests."""
    token = role_tokens["organizer"]
    res = client.post(
        "/api/organizer/events",
        headers=auth_headers(token),
        json={
            "name": "Forecast Test Event",
            "venue": "Test Hall, Pune",
            "starts_at": "2027-06-01T18:00:00Z",
            "seats_per_row": 5,
            "price_tiers": [{"rows": 2, "price": 500}],   # 10 seats
        },
    )
    assert res.status_code == 201
    return res.json()


def _book_and_backdate(client, token, seat_id, days_ago):
    """Book a seat, then push its created_at back in time for the test."""
    from sqlalchemy import update as sa_update

    from core.database import SessionLocal
    from core.models import Booking, utcnow

    res = client.post("/api/bookings", json={"seat_id": seat_id}, headers=auth_headers(token))
    assert res.status_code == 201
    booking_id = res.json()["id"]

    db = SessionLocal()
    try:
        db.execute(
            sa_update(Booking)
            .where(Booking.id == booking_id)
            .values(created_at=utcnow() - timedelta(days=days_ago))
        )
        db.commit()
    finally:
        db.close()


def test_forecast_needs_organizer_role(client, role_tokens, forecast_event):
    res = client.get(
        f"/api/organizer/events/{forecast_event['id']}/forecast",
        headers=auth_headers(role_tokens["attendee"]),
    )
    assert res.status_code == 403


def test_forecast_needs_auth(client, forecast_event):
    res = client.get(f"/api/organizer/events/{forecast_event['id']}/forecast")
    assert res.status_code == 401


def test_forecast_404s_with_too_few_bookings(client, role_tokens, forecast_event):
    res = client.get(
        f"/api/organizer/events/{forecast_event['id']}/forecast",
        headers=auth_headers(role_tokens["organizer"]),
    )
    assert res.status_code == 404


def test_forecast_projects_from_real_booking_history(client, tokens, role_tokens, forecast_event):
    seats = client.get(f"/api/events/{forecast_event['id']}/seats").json()

    for i in range(MIN_BOOKINGS):
        _book_and_backdate(client, tokens[i], seats[i]["id"], days_ago=i)

    res = client.get(
        f"/api/organizer/events/{forecast_event['id']}/forecast",
        headers=auth_headers(role_tokens["organizer"]),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["bookings_analyzed"] == MIN_BOOKINGS
    assert body["rate_per_day"] > 0
    # Exactly one of the two projections is set, never both, never neither.
    assert (body["sellout_date"] is None) != (body["projected_percent_sold"] is None)
