"""
Shared fixtures for the integration suite.

These are not unit tests. They send real HTTP requests to a running API,
because race conditions only appear when the full stack (uvicorn + Redis +
Postgres) is running. Mocking would have missed the bug the load test caught.

Run:
    docker compose exec backend pytest tests/ -v

Logins are session-scoped: one set of tokens for the whole run. Each bcrypt
login costs ~100 ms and the concurrency tests need 40 distinct users, so
logging in again for every module would add seconds per file for nothing.
"""

import httpx
import pytest

from helpers import BASE_URL, CONCURRENCY, auth_headers, login


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c:
        yield c


@pytest.fixture(scope="session")
def tokens(client):
    """
    Each concurrent "user" has their own token.

    Distinct users are necessary — if the same user requests a lock again,
    they receive a 200 with `already_owned`, invalidating the contention test.
    """
    emails = ["demo@seatpulse.dev"] + [
        f"user{i}@seatpulse.dev" for i in range(1, CONCURRENCY)
    ]
    try:
        return [login(client, e) for e in emails]
    except httpx.HTTPStatusError:
        pytest.skip("Test users missing — run 'python seed.py'")


@pytest.fixture
def free_seat(client, tokens):
    """Take an available seat, clean it up after the test."""
    seats = client.get("/api/events/1/seats").json()
    available = [s for s in seats if s["status"] == "available"]
    if not available:
        pytest.skip("No available seats — run 'python reset_state.py'")

    seat = available[-1]        # use the last one to avoid conflict with UI
    yield seat

    # Cleanup: release each user's lock, then cancel the booking
    for token in tokens:
        client.delete(f"/api/seats/{seat['id']}/lock", headers=auth_headers(token))

    for token in tokens:
        for b in client.get("/api/bookings", headers=auth_headers(token)).json():
            if b["seat_id"] == seat["id"] and b["status"] == "confirmed":
                client.delete(f"/api/bookings/{b['id']}", headers=auth_headers(token))


@pytest.fixture(scope="session")
def role_tokens(client):
    """Tokens for all three roles. seed.py creates these accounts."""
    accounts = {
        "attendee": "demo@seatpulse.dev",
        "organizer": "organizer@seatpulse.dev",
        "admin": "admin@seatpulse.dev",
    }
    try:
        return {role: login(client, email) for role, email in accounts.items()}
    except httpx.HTTPStatusError:
        pytest.skip("Role accounts missing — run 'python seed.py'")
