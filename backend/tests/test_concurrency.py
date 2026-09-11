"""
Concurrency + auth tests — against a running API.

These are not unit tests. They send real HTTP requests because race
conditions only appear when the full stack (uvicorn + Redis + Postgres)
is running. Mocking would have missed the bug caught by the load test.

Run:
    docker compose exec backend pytest tests/ -v
"""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

# "backend" from inside the container, "localhost" from the host
BASE_URL = os.getenv("TEST_BASE_URL", "http://backend:8000")

# seed.py assigns this password to all test users
PASSWORD = "demo1234"
CONCURRENCY = 40

# Unique suffix for each pytest run.
#
# ⚠️ Idempotency keys were previously fixed (`test-100-once`). They persist
# in Redis until TTL, meaning the NEXT test run would replay on the same key:
# it would return 201, but no new booking was created — and the test would
# fail on "0 bookings found".
#
# This bug was caught on a multi-worker stack and initially misidentified as
# a multi-worker issue. It wasn't — tests relied on reset_state.py, which
# could be skipped. Now each run uses its own keys, eliminating this dependency.
RUN_ID = uuid.uuid4().hex[:8]


def _token(client: httpx.Client, email: str) -> str:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    res.raise_for_status()
    return res.json()["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c:
        yield c


@pytest.fixture(scope="module")
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
        return [_token(client, e) for e in emails]
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
        client.delete(f"/api/seats/{seat['id']}/lock", headers=_headers(token))

    for token in tokens:
        for b in client.get("/api/bookings", headers=_headers(token)).json():
            if b["seat_id"] == seat["id"] and b["status"] == "confirmed":
                client.delete(f"/api/bookings/{b['id']}", headers=_headers(token))


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "healthy"
    assert body["database"] == "connected"
    assert body["redis"] == "connected"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_protected_routes_need_a_token(client):
    """All booking/lock/bookings endpoints return 401 without a token."""
    assert client.post("/api/bookings", json={"seat_id": 1}).status_code == 401
    assert client.post("/api/seats/1/lock").status_code == 401
    assert client.get("/api/bookings").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_garbage_token_rejected(client):
    res = client.get("/api/auth/me", headers=_headers("not-a-real-token"))
    assert res.status_code == 401


def test_login_wrong_password(client):
    res = client.post(
        "/api/auth/login", json={"email": "demo@seatpulse.dev", "password": "galat"}
    )
    assert res.status_code == 401
    # Use the same message for unknown emails to prevent user enumeration.
    assert res.json()["detail"] == "Invalid email or password"


def test_login_unknown_email_same_message(client):
    res = client.post(
        "/api/auth/login", json={"email": "nonexistent@example.dev", "password": "anything"}
    )
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid email or password"


def test_refresh_rotates_and_old_token_dies(client):
    """Old refresh token should be invalidated after refresh."""
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c:
        c.post("/api/auth/login", json={"email": "demo@seatpulse.dev", "password": PASSWORD})
        old_cookie = c.cookies.get("seatpulse_refresh")
        assert old_cookie

        assert c.post("/api/auth/refresh").status_code == 200
        assert c.cookies.get("seatpulse_refresh") != old_cookie   # rotated

    # Old cookie should now be rejected
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c2:
        c2.cookies.set("seatpulse_refresh", old_cookie)
        assert c2.post("/api/auth/refresh").status_code == 401


def test_logout_kills_refresh_token(client):
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c:
        c.post("/api/auth/login", json={"email": "demo@seatpulse.dev", "password": PASSWORD})
        assert c.post("/api/auth/logout").status_code == 204
        assert c.post("/api/auth/refresh").status_code == 401


def test_cannot_cancel_someone_elses_booking(client, tokens, free_seat):
    """IDOR check — cannot cancel someone else's booking."""
    seat_id = free_seat["id"]
    owner, attacker = tokens[0], tokens[1]

    res = client.post("/api/bookings", json={"seat_id": seat_id}, headers=_headers(owner))
    assert res.status_code == 201
    booking_id = res.json()["id"]

    # 404 (not 403) — attacker should not know if the booking exists
    assert client.delete(f"/api/bookings/{booking_id}", headers=_headers(attacker)).status_code == 404
    assert client.delete(f"/api/bookings/{booking_id}", headers=_headers(owner)).status_code == 200


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def role_tokens(client):
    """Tokens for all three roles. seed.py creates these accounts."""
    accounts = {
        "attendee": "demo@seatpulse.dev",
        "organizer": "organizer@seatpulse.dev",
        "admin": "admin@seatpulse.dev",
    }
    try:
        return {role: _token(client, email) for role, email in accounts.items()}
    except httpx.HTTPStatusError:
        pytest.skip("Role accounts missing — run 'python seed.py'")


def test_role_comes_through_in_me(client, role_tokens):
    for role, token in role_tokens.items():
        assert client.get("/api/auth/me", headers=_headers(token)).json()["role"] == role


def test_attendee_cannot_touch_organizer_or_admin(client, role_tokens):
    """Most basic RBAC check."""
    t = _headers(role_tokens["attendee"])
    assert client.get("/api/organizer/events", headers=t).status_code == 403
    assert client.get("/api/admin/stats", headers=t).status_code == 403


def test_organizer_cannot_reach_admin(client, role_tokens):
    """Being an organizer does not mean being an admin."""
    assert client.get(
        "/api/admin/stats", headers=_headers(role_tokens["organizer"])
    ).status_code == 403


def test_admin_can_reach_everything(client, role_tokens):
    t = _headers(role_tokens["admin"])
    assert client.get("/api/admin/stats", headers=t).status_code == 200
    assert client.get("/api/organizer/events", headers=t).status_code == 200


def test_organizer_creates_event_with_priced_rows(client, role_tokens):
    """Are seats created correctly with price tiers?"""
    token = role_tokens["organizer"]

    res = client.post(
        "/api/organizer/events",
        headers=_headers(token),
        json={
            "name": "Pytest Event",
            "venue": "Test Hall, Pune",
            "starts_at": "2027-01-01T18:00:00Z",
            "category": "Comedy",
            "seats_per_row": 4,
            "price_tiers": [{"rows": 1, "price": 1500}, {"rows": 2, "price": 500}],
        },
    )
    assert res.status_code == 201
    event = res.json()
    assert event["total_seats"] == 3 * 4      # 3 rows x 4 seats
    assert event["available_seats"] == 12

    # Seats were created, according to pricing tiers
    seats = client.get(f"/api/events/{event['id']}/seats").json()
    assert len(seats) == 12
    assert {s["price"] for s in seats if s["row_label"] == "A"} == {1500}
    assert {s["price"] for s in seats if s["row_label"] in ("B", "C")} == {500}

    # Cleanup: deletion will succeed if there are no bookings.
    assert client.delete(
        f"/api/organizer/events/{event['id']}", headers=_headers(token)
    ).status_code == 204


def test_attendee_cannot_create_event(client, role_tokens):
    res = client.post(
        "/api/organizer/events",
        headers=_headers(role_tokens["attendee"]),
        json={
            "name": "Should Fail",
            "venue": "Nowhere",
            "starts_at": "2027-01-01T18:00:00Z",
            "seats_per_row": 2,
            "price_tiers": [{"rows": 1, "price": 100}],
        },
    )
    assert res.status_code == 403


def test_organizer_cannot_touch_another_organizers_event(client, role_tokens, tokens):
    """
    ⭐ Most important RBAC test.

    Passing the role check does not mean you own every resource.
    Ownership must be checked separately.
    """
    owner = role_tokens["organizer"]

    created = client.post(
        "/api/organizer/events",
        headers=_headers(owner),
        json={
            "name": "Ownership Test",
            "venue": "Test Hall",
            "starts_at": "2027-02-01T18:00:00Z",
            "seats_per_row": 2,
            "price_tiers": [{"rows": 1, "price": 100}],
        },
    ).json()

    # Let's try making user1 an organizer — they have the role, but not the event
    admin = _headers(role_tokens["admin"])
    other = _token(client, "user1@seatpulse.dev")

    # If user1 is not an organizer, they receive 403; if they are, they receive 404 (ownership).
    # Both indicate "no access" for different reasons.
    patch = client.patch(
        f"/api/organizer/events/{created['id']}",
        headers=_headers(other),
        json={"name": "HACKED"},
    )
    assert patch.status_code in (403, 404)

    # Owner can edit their own event
    assert client.patch(
        f"/api/organizer/events/{created['id']}",
        headers=_headers(owner),
        json={"venue": "Updated Hall"},
    ).status_code == 200

    # Admin can also edit
    assert client.patch(
        f"/api/organizer/events/{created['id']}", headers=admin, json={"venue": "Admin Hall"}
    ).status_code == 200

    client.delete(f"/api/organizer/events/{created['id']}", headers=_headers(owner))


def test_event_with_bookings_cannot_be_deleted(client, role_tokens):
    """
    ⚠️ Business rule: paid tickets must never disappear.

    Cascade delete is enabled, so without this guard, a DELETE would wipe out
    tickets purchased by users.
    """
    owner = role_tokens["organizer"]
    attendee = role_tokens["attendee"]

    created = client.post(
        "/api/organizer/events",
        headers=_headers(owner),
        json={
            "name": "Delete Guard Test",
            "venue": "Test Hall",
            "starts_at": "2027-03-01T18:00:00Z",
            "seats_per_row": 2,
            "price_tiers": [{"rows": 1, "price": 100}],
        },
    ).json()

    seat = client.get(f"/api/events/{created['id']}/seats").json()[0]
    booking = client.post(
        "/api/bookings", json={"seat_id": seat["id"]}, headers=_headers(attendee)
    )
    assert booking.status_code == 201

    # Delete should now be blocked
    blocked = client.delete(f"/api/organizer/events/{created['id']}", headers=_headers(owner))
    assert blocked.status_code == 409

    # Cancel the booking -> delete will now work
    client.delete(f"/api/bookings/{booking.json()['id']}", headers=_headers(attendee))
    assert client.delete(
        f"/api/organizer/events/{created['id']}", headers=_headers(owner)
    ).status_code == 204


def test_seat_layout_limits_are_enforced(client, role_tokens):
    """Cannot create more than 26 rows (A-Z)."""
    res = client.post(
        "/api/organizer/events",
        headers=_headers(role_tokens["organizer"]),
        json={
            "name": "Too Many Rows",
            "venue": "Test Hall",
            "starts_at": "2027-04-01T18:00:00Z",
            "seats_per_row": 10,
            "price_tiers": [{"rows": 20, "price": 100}, {"rows": 20, "price": 50}],
        },
    )
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def test_rate_limit_blocks_a_burst(client, tokens, free_seat):
    """
    If a user sends 40 requests at once — some should receive 429.

    The token bucket allows a burst of 15, then refills at 5/s. The refill
    continues during the serial curl loop, so we check for "some 429s",
    not "exactly 25" — that would be flaky.
    """
    seat_id = free_seat['id']
    token = tokens[3]

    codes = [
        client.post(f"/api/seats/{seat_id}/lock", headers=_headers(token)).status_code
        for _ in range(40)
    ]

    assert 429 in codes, f"Rate limit not applied: {sorted(set(codes))}"
    # Initial requests should pass — the limiter shouldn't block everything
    assert codes[0] in (200, 409)


def test_rate_limit_sends_headers(client, tokens, free_seat):
    """Client should know how close it is to the limit."""
    res = client.post(
        f"/api/seats/{free_seat['id']}/lock", headers=_headers(tokens[4])
    )
    assert "X-RateLimit-Limit" in res.headers
    assert "X-RateLimit-Remaining" in res.headers


def test_rate_limit_is_per_user_not_global(client, tokens, free_seat):
    """
    One user being blocked should not affect another user.

    This is the most important rate limit test — a global limiter would
    shut down the entire system due to one bot.
    """
    seat_id = free_seat['id']
    victim, other = tokens[5], tokens[6]

    # Exhaust one user's bucket
    for _ in range(40):
        client.post(f"/api/seats/{seat_id}/lock", headers=_headers(victim))

    # The other user should not receive a 429
    res = client.post(f"/api/seats/{seat_id}/lock", headers=_headers(other))
    assert res.status_code != 429, "One user's limit blocked another"


def test_wrong_password_eventually_rate_limited(client):
    """Brute force protection — 429 on repeated wrong passwords."""
    email = "user9@seatpulse.dev"

    codes = [
        client.post(
            "/api/auth/login", json={"email": email, "password": f"wrong{i}"}
        ).status_code
        for i in range(12)
    ]

    assert 429 in codes, f"Brute force not stopped: {sorted(set(codes))}"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_same_idempotency_key_returns_same_booking(client, tokens, free_seat):
    """
    ⭐ Real test for double-clicks.

    Same key again -> same booking, and only ONE row in the database.
    """
    seat_id = free_seat['id']
    token = tokens[0]
    headers = {**_headers(token), "Idempotency-Key": f"test-{seat_id}-once-{RUN_ID}"}

    first = client.post("/api/bookings", json={"seat_id": seat_id}, headers=headers)
    assert first.status_code == 201

    second = client.post("/api/bookings", json={"seat_id": seat_id}, headers=headers)
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"], "A different booking was created!"
    assert second.headers.get("X-Idempotent-Replay") == "true"

    # Most important check — how many bookings were actually created in the DB
    mine = client.get("/api/bookings", headers=_headers(token)).json()
    for_seat = [b for b in mine if b["seat_id"] == seat_id and b["status"] == "confirmed"]
    assert len(for_seat) == 1


def test_same_key_different_body_is_rejected(client, tokens, free_seat):
    """Same key with different data = bug or attack. Do not silently return the old response."""
    seat_id = free_seat['id']
    headers = {**_headers(tokens[0]), "Idempotency-Key": f"test-{seat_id}-mismatch-{RUN_ID}"}

    assert client.post("/api/bookings", json={"seat_id": seat_id}, headers=headers).status_code == 201

    res = client.post("/api/bookings", json={"seat_id": seat_id + 1}, headers=headers)
    assert res.status_code == 422


def test_booking_works_without_idempotency_key(client, tokens, free_seat):
    """Header should be optional — legacy clients should not break."""
    res = client.post(
        "/api/bookings", json={"seat_id": free_seat['id']}, headers=_headers(tokens[0])
    )
    assert res.status_code == 201


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

def test_only_one_user_gets_the_lock(client, tokens, free_seat):
    """40 users, one seat — only one should get the lock."""
    seat_id = free_seat["id"]

    def try_lock(token):
        return client.post(f"/api/seats/{seat_id}/lock", headers=_headers(token)).status_code

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        codes = list(pool.map(try_lock, tokens))

    assert codes.count(200) == 1, f"Expected exactly 1 lock, got {codes.count(200)}"
    assert codes.count(409) == len(tokens) - 1


def test_no_double_booking(client, tokens, free_seat):
    """40 users booking simultaneously — exactly 1 booking in the database."""
    seat_id = free_seat["id"]

    def try_book(token):
        return client.post(
            "/api/bookings", json={"seat_id": seat_id}, headers=_headers(token)
        ).status_code

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        codes = list(pool.map(try_book, tokens))

    assert codes.count(201) == 1, f"Expected exactly 1 booking, got {codes.count(201)}"
    assert client.get(f"/api/seats/{seat_id}").json()["status"] == "booked"


def test_lock_blocks_other_users_booking(client, tokens, free_seat):
    """If one user holds a seat, another cannot book it."""
    seat_id = free_seat["id"]
    holder, other = tokens[1], tokens[2]

    assert client.post(f"/api/seats/{seat_id}/lock", headers=_headers(holder)).status_code == 200
    assert client.post("/api/bookings", json={"seat_id": seat_id}, headers=_headers(other)).status_code == 409
    # The lock holder can book the seat
    assert client.post("/api/bookings", json={"seat_id": seat_id}, headers=_headers(holder)).status_code == 201


def test_cannot_release_someone_elses_lock(client, tokens, free_seat):
    """Lua script prevents unauthorized lock release."""
    seat_id = free_seat["id"]
    holder, other = tokens[1], tokens[2]

    client.post(f"/api/seats/{seat_id}/lock", headers=_headers(holder))

    res = client.delete(f"/api/seats/{seat_id}/lock", headers=_headers(other))
    assert res.json()["released"] is False

    owner_id = client.get("/api/auth/me", headers=_headers(holder)).json()["id"]
    assert client.get(f"/api/seats/{seat_id}/lock").json()["locked_by"] == owner_id


def test_version_increments_on_change(client, tokens, free_seat):
    """Version must increment on state change — required for optimistic locking."""
    seat_id = free_seat["id"]
    token = tokens[1]

    before = client.get(f"/api/seats/{seat_id}").json()["version"]

    client.post(f"/api/seats/{seat_id}/lock", headers=_headers(token))
    after_lock = client.get(f"/api/seats/{seat_id}").json()["version"]
    assert after_lock > before

    client.post("/api/bookings", json={"seat_id": seat_id}, headers=_headers(token))
    assert client.get(f"/api/seats/{seat_id}").json()["version"] > after_lock


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

def _checkout(client, token, seat_id):
    """Helper to hold a seat and initiate checkout."""
    client.post(f"/api/seats/{seat_id}/lock", headers=_headers(token))
    return client.post(
        "/api/payments/checkout", json={"seat_id": seat_id}, headers=_headers(token)
    )


def test_checkout_moves_seat_to_payment_pending(client, tokens, free_seat):
    """After checkout, the seat moves past the hold state but is NOT booked."""
    seat_id = free_seat["id"]

    res = _checkout(client, tokens[0], seat_id)
    assert res.status_code == 201
    assert res.json()["provider"] in ("mock", "stripe")

    seat = client.get(f"/api/seats/{seat_id}").json()
    assert seat["status"] == "payment_pending"

    # ⭐ Most important: no booking created yet
    mine = client.get("/api/bookings", headers=_headers(tokens[0])).json()
    assert not [b for b in mine if b["seat_id"] == seat_id and b["status"] == "confirmed"]


def test_another_user_cannot_checkout_held_seat(client, tokens, free_seat):
    """One user's hold prevents another's checkout — 409."""
    seat_id = free_seat["id"]
    _checkout(client, tokens[0], seat_id)

    res = client.post(
        "/api/payments/checkout", json={"seat_id": seat_id}, headers=_headers(tokens[1])
    )
    assert res.status_code == 409


def test_successful_payment_creates_exactly_one_booking(client, tokens, free_seat):
    """Happy path — payment succeeds, booking created, seat booked."""
    seat_id = free_seat["id"]
    token = tokens[0]

    payment_id = _checkout(client, token, seat_id).json()["payment_id"]

    res = client.post(
        f"/api/payments/{payment_id}/simulate",
        json={"outcome": "success"},
        headers=_headers(token),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "succeeded"
    assert body["booking_id"] is not None

    assert client.get(f"/api/seats/{seat_id}").json()["status"] == "booked"

    mine = client.get("/api/bookings", headers=_headers(token)).json()
    confirmed = [b for b in mine if b["seat_id"] == seat_id and b["status"] == "confirmed"]
    assert len(confirmed) == 1


def test_fulfilment_is_idempotent(client, tokens, free_seat):
    """
    ⭐ Webhooks are AT-LEAST-ONCE — the gateway may send the same event twice.
    The second request should return the existing booking, not create a new one.
    """
    seat_id = free_seat["id"]
    token = tokens[0]

    payment_id = _checkout(client, token, seat_id).json()["payment_id"]

    first = client.post(
        f"/api/payments/{payment_id}/simulate",
        json={"outcome": "success"},
        headers=_headers(token),
    ).json()

    second = client.post(
        f"/api/payments/{payment_id}/simulate",
        json={"outcome": "success"},
        headers=_headers(token),
    ).json()

    assert first["booking_id"] == second["booking_id"], "A new booking was created on the second attempt!"

    # Only one in the DB
    mine = client.get("/api/bookings", headers=_headers(token)).json()
    assert len([b for b in mine if b["seat_id"] == seat_id and b["status"] == "confirmed"]) == 1


def test_failed_payment_releases_the_seat(client, tokens, free_seat):
    """Payment failure — seat released, no booking created."""
    seat_id = free_seat["id"]
    token = tokens[0]

    payment_id = _checkout(client, token, seat_id).json()["payment_id"]

    res = client.post(
        f"/api/payments/{payment_id}/simulate",
        json={"outcome": "fail"},
        headers=_headers(token),
    ).json()
    assert res["status"] == "failed"
    assert res["booking_id"] is None

    assert client.get(f"/api/seats/{seat_id}").json()["status"] == "available"

    mine = client.get("/api/bookings", headers=_headers(token)).json()
    assert not [b for b in mine if b["seat_id"] == seat_id and b["status"] == "confirmed"]


def test_cannot_see_or_settle_someone_elses_payment(client, tokens, free_seat):
    """IDOR — prevent viewing or settling another user's payment."""
    seat_id = free_seat["id"]
    payment_id = _checkout(client, tokens[0], seat_id).json()["payment_id"]

    attacker = _headers(tokens[1])
    assert client.get(f"/api/payments/{payment_id}", headers=attacker).status_code == 404
    assert client.post(
        f"/api/payments/{payment_id}/simulate", json={"outcome": "success"}, headers=attacker
    ).status_code == 404


def test_webhook_rejects_bad_signature(client):
    """
    ⭐ Webhook endpoint is not authenticated — the signature is the only auth.

    Without this, anyone could POST and claim a free ticket.
    """
    res = client.post(
        "/api/payments/webhook",
        content=b'{"type":"checkout.session.completed"}',
        headers={"stripe-signature": "t=1,v1=deadbeef"},
    )
    assert res.status_code == 400


def test_webhook_without_signature_is_rejected(client):
    res = client.post("/api/payments/webhook", content=b"{}")
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# Tickets (background worker)
# ---------------------------------------------------------------------------

def _wait_for_ticket(client, token, seat_id, timeout=15):
    """
    The worker runs in the background — poll to wait for the ticket.

    ⚠️ Avoided fixed `sleep`. It causes flakiness on slow machines and wastes time on fast ones.
    """
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        bookings = client.get("/api/bookings", headers=_headers(token)).json()
        mine = [b for b in bookings if b["seat_id"] == seat_id and b["status"] == "confirmed"]
        if mine and mine[0]["ticket_status"] != "pending":
            return mine[0]
        time.sleep(0.4)
    return None


def test_booking_starts_with_a_pending_ticket(client, tokens, free_seat):
    """
    Booking is confirmed immediately — the ticket is generated later.

    ⭐ This is the whole point of this phase: the API does not make the user wait for 2-3 seconds.
    """
    seat_id = free_seat["id"]
    res = client.post("/api/bookings", json={"seat_id": seat_id}, headers=_headers(tokens[0]))
    assert res.status_code == 201

    bookings = client.get("/api/bookings", headers=_headers(tokens[0])).json()
    mine = [b for b in bookings if b["seat_id"] == seat_id][0]
    assert mine["ticket_status"] in ("pending", "ready")


def test_worker_generates_a_downloadable_ticket(client, tokens, free_seat):
    """End-to-end: booking → worker → PDF download."""
    seat_id = free_seat["id"]
    token = tokens[0]

    client.post("/api/bookings", json={"seat_id": seat_id}, headers=_headers(token))

    booking = _wait_for_ticket(client, token, seat_id)
    if booking is None:
        pytest.skip("Is the worker running? `docker compose up -d worker`")

    assert booking["ticket_status"] == "ready"

    res = client.get(f"/api/bookings/{booking['id']}/ticket", headers=_headers(token))
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    # Is it a real PDF? Check the header — status 200 is not enough.
    assert res.content[:5] == b"%PDF-"
    assert len(res.content) > 1000


def test_cannot_download_someone_elses_ticket(client, tokens, free_seat):
    """
    ⚠️ The most critical ticket test.

    The ticket contains a QR code. Downloading someone else's ticket = free entry.
    """
    seat_id = free_seat["id"]
    owner, attacker = tokens[0], tokens[1]

    client.post("/api/bookings", json={"seat_id": seat_id}, headers=_headers(owner))
    booking = _wait_for_ticket(client, owner, seat_id)
    if booking is None:
        pytest.skip("Worker is not running")

    res = client.get(f"/api/bookings/{booking['id']}/ticket", headers=_headers(attacker))
    assert res.status_code == 404      # Not 403 — hiding existence


def test_ticket_needs_authentication(client, tokens, free_seat):
    seat_id = free_seat["id"]
    client.post("/api/bookings", json={"seat_id": seat_id}, headers=_headers(tokens[0]))
    booking = _wait_for_ticket(client, tokens[0], seat_id)
    if booking is None:
        pytest.skip("Worker is not running")

    assert client.get(f"/api/bookings/{booking['id']}/ticket").status_code == 401


def test_qr_token_is_not_the_booking_id(client, tokens, free_seat):
    """
    ⚠️ The QR should not contain a sequential ID — anyone could generate a QR for 1, 2, or 3 and enter the gate.
    """
    from sqlalchemy import select

    seat_id = free_seat["id"]
    client.post("/api/bookings", json={"seat_id": seat_id}, headers=_headers(tokens[0]))
    booking = _wait_for_ticket(client, tokens[0], seat_id)
    if booking is None:
        pytest.skip("Worker is not running")

    # The token is in the DB and is long/random.
    from database import SessionLocal
    from models import Booking

    db = SessionLocal()
    try:
        row = db.get(Booking, booking["id"])
        assert row.qr_token
        assert len(row.qr_token) >= 24
        assert str(booking["id"]) != row.qr_token
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Gate check-in
# ---------------------------------------------------------------------------

def _booked_with_ticket(client, token, seat_id):
    """Book and wait for the ticket to be ready — return the QR token."""
    client.post("/api/bookings", json={"seat_id": seat_id}, headers=_headers(token))
    booking = _wait_for_ticket(client, token, seat_id)
    if booking is None or booking["ticket_status"] != "ready":
        pytest.skip("Worker is not running")

    from database import SessionLocal
    from models import Booking

    db = SessionLocal()
    try:
        return booking, db.get(Booking, booking["id"]).qr_token
    finally:
        db.close()


def test_valid_ticket_checks_in(client, tokens, role_tokens, free_seat):
    seat_id = free_seat["id"]
    _, qr = _booked_with_ticket(client, tokens[0], seat_id)

    res = client.post(
        "/api/checkin", json={"token": qr}, headers=_headers(role_tokens["organizer"])
    ).json()

    assert res["ok"] is True
    assert res["reason"] == "checked_in"
    assert res["seat_label"]
    assert res["checked_in_at"]


def test_same_qr_cannot_be_used_twice(client, tokens, role_tokens, free_seat):
    """
    ⭐ The core test for this phase.

    If two people take a screenshot of the same QR and go to different gates — neither should be allowed in.
    """
    seat_id = free_seat["id"]
    _, qr = _booked_with_ticket(client, tokens[0], seat_id)
    gate = _headers(role_tokens["organizer"])

    first = client.post("/api/checkin", json={"token": qr}, headers=gate).json()
    second = client.post("/api/checkin", json={"token": qr}, headers=gate).json()

    assert first["ok"] is True
    assert second["ok"] is False
    assert second["reason"] == "already_checked_in"
    # The "when" should also be returned on duplicates — this is what is asked at the gate.
    assert second["checked_in_at"] == first["checked_in_at"]


def test_concurrent_scans_admit_exactly_one(client, tokens, role_tokens, free_seat):
    """
    ⭐ The real race — 10 gates simultaneously.

    The same "exactly once" problem as in seat booking, just in a different context.
    """
    seat_id = free_seat["id"]
    _, qr = _booked_with_ticket(client, tokens[0], seat_id)
    gate = _headers(role_tokens["organizer"])

    def scan(_):
        return client.post("/api/checkin", json={"token": qr}, headers=gate).json()["reason"]

    with ThreadPoolExecutor(max_workers=10) as pool:
        reasons = list(pool.map(scan, range(10)))

    assert reasons.count("checked_in") == 1, f"More than one entry found: {reasons}"
    assert reasons.count("already_checked_in") == 9


def test_invalid_token_is_rejected(client, role_tokens):
    res = client.post(
        "/api/checkin",
        json={"token": "this-token-does-not-exist-at-all"},
        headers=_headers(role_tokens["organizer"]),
    ).json()

    assert res["ok"] is False
    assert res["reason"] == "invalid_ticket"
    # ⚠️ No details should be leaked — otherwise, tokens could be brute-forced.
    assert res["booking_id"] is None
    assert res["seat_label"] is None


def test_attendee_cannot_scan_tickets(client, tokens, role_tokens, free_seat):
    """The gate portal is only for organizers/admins."""
    seat_id = free_seat["id"]
    _, qr = _booked_with_ticket(client, tokens[0], seat_id)

    res = client.post(
        "/api/checkin", json={"token": qr}, headers=_headers(role_tokens["attendee"])
    )
    assert res.status_code == 403


def test_cancelled_booking_cannot_check_in(client, tokens, role_tokens, free_seat):
    seat_id = free_seat["id"]
    booking, qr = _booked_with_ticket(client, tokens[0], seat_id)

    client.delete(f"/api/bookings/{booking['id']}", headers=_headers(tokens[0]))

    res = client.post(
        "/api/checkin", json={"token": qr}, headers=_headers(role_tokens["organizer"])
    ).json()
    assert res["ok"] is False
    assert res["reason"] == "booking_cancelled"


def test_checkin_stats(client, role_tokens):
    res = client.get(
        "/api/checkin/events/1/stats", headers=_headers(role_tokens["admin"])
    )
    assert res.status_code == 200
    body = res.json()
    assert body["tickets_sold"] >= body["checked_in"]
    assert body["remaining"] == body["tickets_sold"] - body["checked_in"]


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

from pricing import apply, multiplier_for, pricing_for_event


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
        headers=_headers(token),
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
        for b in client.get("/api/bookings", headers=_headers(t)).json():
            if b["event_id"] == event["id"] and b["status"] == "confirmed":
                client.delete(f"/api/bookings/{b['id']}", headers=_headers(t))
    tokens_cache.clear()
    client.delete(f"/api/organizer/events/{event['id']}", headers=_headers(token))


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
        headers=_headers(tokens[0]),
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
        f"/api/seats/{a_seat['id']}/lock", headers=_headers(tokens[0])
    ).json()
    quoted = lock["price"]
    assert quoted == 1000.0

    # B buys — demand increases
    assert client.post(
        "/api/bookings", headers=_headers(tokens[1]), json={"seat_id": b_seat["id"]}
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
        "/api/bookings", headers=_headers(tokens[0]), json={"seat_id": a_seat["id"]}
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

    client.post(f"/api/seats/{seat['id']}/lock", headers=_headers(tokens[0]))
    client.delete(f"/api/seats/{seat['id']}/lock", headers=_headers(tokens[0]))

    fresh = client.get(f"/api/seats/{seat['id']}").json()
    assert fresh["held_price"] is None


def test_organizer_can_turn_surge_off(client, role_tokens, surge_event):
    """Allow organizer to disable surge if sales are slow — revert to base price."""
    res = client.patch(
        f"/api/organizer/events/{surge_event['id']}",
        headers=_headers(role_tokens["organizer"]),
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
        headers=_headers(role_tokens["organizer"]),
        json={"price_tiers": [{"rows": 2, "price": 99999}]},
    )
    seats = client.get(f"/api/events/{surge_event['id']}/seats").json()
    assert all(s["price"] == 1000.0 for s in seats)


def test_absurd_surge_settings_are_rejected(client, role_tokens, surge_event):
    """Server should reject accidental typos like demand_factor=50."""
    res = client.patch(
        f"/api/organizer/events/{surge_event['id']}",
        headers=_headers(role_tokens["organizer"]),
        json={"demand_factor": 50},
    )
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# Phase 15 — Locking strategies
#
# ⭐ These tests must pass in both modes.
#
# When BENCHMARK_MODE is off, the server ignores the `strategy` param and runs
# optimistic. When on, it runs the pessimistic path. In both cases, one thing
# must hold true: **one seat, one booking**.
#
# The test is written for this invariant, not internal details — so it is not
# skipped based on mode, and remains meaningful even if benchmark mode is
# accidentally left on.
# ---------------------------------------------------------------------------

def test_pessimistic_strategy_also_prevents_double_booking(client, tokens, free_seat):
    """
    Pessimistic path must also prevent overselling.

    This is the first question of the benchmark: are both strategies CORRECT?
    "Which is faster" is irrelevant if one is wrong.
    """
    seat_id = free_seat["id"]

    def book(token):
        return client.post(
            "/api/bookings?strategy=pessimistic&redis_lock=off",
            headers=_headers(token),
            json={"seat_id": seat_id},
        ).status_code

    with ThreadPoolExecutor(max_workers=len(tokens)) as pool:
        codes = list(pool.map(book, tokens))

    assert codes.count(201) == 1, f"exactly one booking expected, got: {codes}"
    # Others should get 409 (or 429 for rate limit) — never 500
    assert all(c in (201, 409, 429) for c in codes), codes


def test_unknown_strategy_falls_back_to_optimistic(client, tokens, free_seat):
    """
    Server should fall back to a safe default on garbage `strategy` values, not 500.

    This seems minor but is critical: this param is not in the public API, so
    anyone can send anything. Crashing on an unknown value would be a DoS.
    """
    res = client.post(
        "/api/bookings?strategy=../../etc/passwd",
        headers=_headers(tokens[0]),
        json={"seat_id": free_seat["id"]},
    )
    assert res.status_code == 201


def test_both_strategies_write_identical_seat_state(client, tokens, role_tokens):
    """
    Seat state must look identical after both strategies.

    If the pessimistic path forgets to increment `version` (not needed due to
    row locks), WebSocket clients won't see the update — and the benchmark
    would be measuring two DIFFERENT things.
    """
    token = role_tokens["organizer"]
    states = []

    for strategy in ("optimistic", "pessimistic"):
        ev = client.post(
            "/api/organizer/events",
            headers=_headers(token),
            json={
                "name": f"Strategy Test {strategy}",
                "venue": "Test Hall",
                "starts_at": "2027-11-11T18:00:00Z",
                "seats_per_row": 2,
                "price_tiers": [{"rows": 1, "price": 500}],
            },
        ).json()

        seats = client.get(f"/api/events/{ev['id']}/seats").json()
        before = seats[0]

        assert client.post(
            f"/api/bookings?strategy={strategy}&redis_lock=off",
            headers=_headers(tokens[0]),
            json={"seat_id": before["id"]},
        ).status_code == 201

        after = client.get(f"/api/seats/{before['id']}").json()
        states.append({
            "status": after["status"],
            "version_delta": after["version"] - before["version"],
            "locked_by": after["locked_by"],
            "held_price": after["held_price"],
        })

        # cleanup
        for b in client.get("/api/bookings", headers=_headers(tokens[0])).json():
            if b["event_id"] == ev["id"]:
                client.delete(f"/api/bookings/{b['id']}", headers=_headers(tokens[0]))
        client.delete(f"/api/organizer/events/{ev['id']}", headers=_headers(token))

    assert states[0] == states[1], f"strategies left different states: {states}"
    assert states[0]["status"] == "booked"
    assert states[0]["version_delta"] == 1


# ---------------------------------------------------------------------------
# Phase 17 — Group booking (split payment)
#
# The core question here differs from single-seat booking. There, "exactly once"
# meant: one seat, one booking. Here it means: **all or nothing**,
# across N separate payments.
# ---------------------------------------------------------------------------

def _clear_user_rate_limits():
    """
    Clear per-user rate limit buckets (`rl:user:*`).

    ⚠️ This is necessary for test-specific reasons.

    The BOOKING limit is 5 burst / 1 per second. A group test makes multiple
    calls from one user — create group, then checkout each share. Previous
    booking and rate-limit tests in the suite have already exhausted that
    bucket, causing group tests to hit 429s.

    That 429 is a result of test order, not group logic. Therefore, we only
    clear per-user buckets — we don't touch `rl:login:*`, as the brute-force
    test relies on it.
    """
    from redis_client import redis_client

    for key in redis_client.scan_iter("rl:user:*", count=500):
        redis_client.delete(key)


@pytest.fixture
def group_seats(client, tokens):
    """3 available seats — clean up remaining ones after the test."""
    _clear_user_rate_limits()

    seats = client.get("/api/events/1/seats").json()
    available = [s["id"] for s in seats if s["status"] == "available"]
    if len(available) < 3:
        pytest.skip("Not enough available seats — run reset_state.py")

    picked = available[:3]
    yield picked

    # Cleanup: remove remaining bookings. Canceling the group is not enough —
    # confirmed groups cannot be canceled.
    for token in tokens[:6]:
        for b in client.get("/api/bookings", headers=_headers(token)).json():
            if b["seat_id"] in picked and b["status"] == "confirmed":
                client.delete(f"/api/bookings/{b['id']}", headers=_headers(token))


def _make_group(client, token, seat_ids, minutes=30):
    res = client.post(
        "/api/groups",
        headers=_headers(token),
        json={"seat_ids": seat_ids, "deadline_minutes": minutes},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _pay_share(client, token, share_token, share_id):
    """Create a checkout for the share and simulate success via the mock provider."""
    res = client.post(
        f"/api/groups/{share_token}/shares/{share_id}/pay", headers=_headers(token)
    )
    assert res.status_code == 200, res.text
    pid = res.json()["payment_id"]
    return client.post(
        f"/api/payments/{pid}/simulate",
        json={"outcome": "success"},
        headers=_headers(token),
    )


def _seat(client, seat_id):
    return client.get(f"/api/seats/{seat_id}").json()


def test_group_holds_seats_without_booking_them(client, tokens, group_seats):
    """
    Seats are held when a group is created, NOT booked.

    This distinction is the foundation of the feature: no seat is confirmed
    until payment is received.
    """
    group = _make_group(client, tokens[0], group_seats)

    assert group["status"] == "collecting"
    assert group["total_shares"] == 3
    assert group["paid_shares"] == 0

    for seat_id in group_seats:
        assert _seat(client, seat_id)["status"] == "group_held"

    client.delete(f"/api/groups/{group['share_token']}", headers=_headers(tokens[0]))


def test_partial_payment_confirms_nobody(client, tokens, group_seats):
    """
    ⭐ 2 out of 3 paid — no one's seat should be booked.

    This is the real test of "all or nothing".
    """
    group = _make_group(client, tokens[0], group_seats)
    st = group["share_token"]

    client.post(f"/api/groups/{st}/shares/{group['shares'][1]['id']}/claim",
                headers=_headers(tokens[1]))

    _pay_share(client, tokens[0], st, group["shares"][0]["id"])
    _pay_share(client, tokens[1], st, group["shares"][1]["id"])

    after = client.get(f"/api/groups/{st}", headers=_headers(tokens[0])).json()
    assert after["paid_shares"] == 2
    assert after["status"] == "collecting", "Should not confirm with 2 out of 3"

    # not a single seat is booked
    for seat_id in group_seats:
        assert _seat(client, seat_id)["status"] == "group_held"

    client.delete(f"/api/groups/{st}", headers=_headers(tokens[0]))


def test_all_paid_confirms_everyone(client, tokens, group_seats):
    """The final payment confirms everyone at once."""
    group = _make_group(client, tokens[0], group_seats)
    st = group["share_token"]

    for i in (1, 2):
        client.post(f"/api/groups/{st}/shares/{group['shares'][i]['id']}/claim",
                    headers=_headers(tokens[i]))

    for i in (0, 1, 2):
        _pay_share(client, tokens[i], st, group["shares"][i]["id"])

    final = client.get(f"/api/groups/{st}", headers=_headers(tokens[0])).json()
    assert final["status"] == "confirmed"
    assert final["paid_shares"] == 3

    for seat_id in group_seats:
        assert _seat(client, seat_id)["status"] == "booked"

    # Three separate bookings for three different users, not three for one user.
    owners = set()
    for i in (0, 1, 2):
        for b in client.get("/api/bookings", headers=_headers(tokens[i])).json():
            if b["seat_id"] in group_seats and b["status"] == "confirmed":
                owners.add(i)
    assert owners == {0, 1, 2}


def test_expired_group_releases_seats_and_refunds(client, tokens, group_seats):
    """
    ⭐ Deadline passed — seats are released and payments are refunded.

    Shift the deadline back in the DB; a real 30-minute wait is not feasible in tests.
    """
    from datetime import timedelta

    from sqlalchemy import update as sa_update

    from database import SessionLocal
    from groups import expire_due_groups
    from models import GroupBooking, utcnow

    group = _make_group(client, tokens[0], group_seats, minutes=5)
    st = group["share_token"]

    _pay_share(client, tokens[0], st, group["shares"][0]["id"])

    db = SessionLocal()
    try:
        db.execute(
            sa_update(GroupBooking)
            .where(GroupBooking.share_token == st)
            .values(expires_at=utcnow() - timedelta(minutes=1))
        )
        db.commit()
        # Call the job directly — no need to wait for the 30-second cron.
        expire_due_groups(db)
    finally:
        db.close()

    after = client.get(f"/api/groups/{st}", headers=_headers(tokens[0])).json()
    assert after["status"] == "expired"

    # Refund the payer, leave others as unpaid.
    statuses = [s["status"] for s in after["shares"]]
    assert statuses.count("refunded") == 1
    assert statuses.count("unpaid") == 2

    for seat_id in group_seats:
        assert _seat(client, seat_id)["status"] == "available"


def test_pending_payment_dies_with_the_group(client, tokens, group_seats):
    """
    If the group breaks, any open checkout is invalidated.

    The user was on the gateway page when the deadline passed. The best outcome is to avoid charging them entirely — not charging is better than a refund. Therefore, `break_group` expires pending payments.
    """
    from datetime import timedelta

    from sqlalchemy import update as sa_update

    from database import SessionLocal
    from groups import expire_due_groups
    from models import GroupBooking, GroupShare, Payment, utcnow

    group = _make_group(client, tokens[0], group_seats, minutes=5)
    st = group["share_token"]
    share_id = group["shares"][0]["id"]

    res = client.post(f"/api/groups/{st}/shares/{share_id}/pay",
                      headers=_headers(tokens[0]))
    assert res.status_code == 200
    payment_id = res.json()["payment_id"]

    db = SessionLocal()
    try:
        db.execute(
            sa_update(GroupBooking)
            .where(GroupBooking.share_token == st)
            .values(expires_at=utcnow() - timedelta(minutes=1))
        )
        db.commit()
        expire_due_groups(db)

        assert db.get(Payment, payment_id).status == "expired"
        share = db.get(GroupShare, share_id)
        assert share.status == "unpaid", "Should not be 'paid' if no money was charged."
        assert share.booking_id is None
    finally:
        db.close()

    assert _seat(client, group_seats[0])["status"] == "available"


def test_late_webhook_after_expiry_is_refunded_not_booked(client, tokens, group_seats):
    """
    ⭐⭐ The most difficult case: the group has expired, but the gateway reports "payment received".

    The previous test shows we close the checkout. However, the real gateway does not stop when we do — webhooks can arrive late, after the payment has already been processed.

    In that situation, the seat cannot be reclaimed (it was released and perhaps taken by someone else). The only correct response is a **refund**.

    We call `_fulfil` directly here because the `/simulate` endpoint does not handle expired payments, whereas a real webhook would.
    """
    from datetime import timedelta

    from sqlalchemy import update as sa_update

    from database import SessionLocal
    from groups import expire_due_groups
    from models import GroupBooking, GroupShare, Payment, utcnow
    from routers.payments import _fulfil

    group = _make_group(client, tokens[0], group_seats, minutes=5)
    st = group["share_token"]
    share_id = group["shares"][0]["id"]

    res = client.post(f"/api/groups/{st}/shares/{share_id}/pay",
                      headers=_headers(tokens[0]))
    payment_id = res.json()["payment_id"]

    db = SessionLocal()
    try:
        db.execute(
            sa_update(GroupBooking)
            .where(GroupBooking.share_token == st)
            .values(expires_at=utcnow() - timedelta(minutes=1))
        )
        db.commit()
        expire_due_groups(db)

        # Late "succeeded" notification from the gateway.
        _fulfil(db, db.get(Payment, payment_id))

        share = db.get(GroupShare, share_id)
        assert share.status == "refunded",             "Late payments must be refunded."
        assert share.booking_id is None, "No booking should be created for an expired group."
        assert db.get(Payment, payment_id).status == "refunded"
    finally:
        db.close()

    assert _seat(client, group_seats[0])["status"] == "available"


def test_only_one_person_can_claim_a_share(client, tokens, group_seats):
    """Two people claim the same open seat at once — only one may win."""
    group = _make_group(client, tokens[0], group_seats)
    st = group["share_token"]
    open_share = group["shares"][1]["id"]

    def claim(token):
        return client.post(
            f"/api/groups/{st}/shares/{open_share}/claim", headers=_headers(token)
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(claim, [tokens[1], tokens[2]]))

    assert codes.count(200) == 1, f"Exactly one claim expected: {codes}"
    assert codes.count(409) == 1

    client.delete(f"/api/groups/{st}", headers=_headers(tokens[0]))


def test_cannot_pay_someone_elses_share(client, tokens, group_seats):
    """You cannot pay for a share you did not claim."""
    group = _make_group(client, tokens[0], group_seats)
    st = group["share_token"]

    # share[0] belongs to the creator (tokens[0])
    res = client.post(
        f"/api/groups/{st}/shares/{group['shares'][0]['id']}/pay",
        headers=_headers(tokens[1]),
    )
    assert res.status_code == 403

    client.delete(f"/api/groups/{st}", headers=_headers(tokens[0]))


def test_group_creation_is_all_or_nothing(client, tokens, group_seats):
    """
    ⭐ If even one seat is unavailable, the ENTIRE group should fail.

    Partial holds are useless — a user shouldn't be left waiting for a 3rd seat that will never be available.
    """
    # Book one seat
    taken = group_seats[2]
    assert client.post("/api/bookings", json={"seat_id": taken},
                       headers=_headers(tokens[5])).status_code == 201

    res = client.post(
        "/api/groups",
        headers=_headers(tokens[0]),
        json={"seat_ids": group_seats},
    )
    assert res.status_code == 409

    # ⭐ The remaining two seats should be RELEASED — not stuck in group_held.
    for seat_id in group_seats[:2]:
        assert _seat(client, seat_id)["status"] == "available", \
            "Failed group creation left seats in hold."


def test_unknown_share_token_is_404(client, tokens):
    """Guessing a token must not grant access to someone else's group."""
    res = client.get("/api/groups/definitely-not-a-real-token",
                     headers=_headers(tokens[0]))
    assert res.status_code == 404


def test_only_creator_can_cancel(client, tokens, group_seats):
    """A non-creator gets 404, not 403 — existence stays hidden."""
    group = _make_group(client, tokens[0], group_seats)
    st = group["share_token"]

    assert client.delete(f"/api/groups/{st}",
                         headers=_headers(tokens[1])).status_code == 404
    assert client.delete(f"/api/groups/{st}",
                         headers=_headers(tokens[0])).status_code == 200


def test_confirm_and_expiry_race_has_exactly_one_winner(client, tokens, group_seats):
    """
    ⭐⭐ The most difficult test in Phase 17.

    The last person is paying while the expiry job is breaking the group. Exactly one must win, with proper cleanup for the loser:

      confirm wins -> all seats booked, all bookings created
      expire wins  -> all seats available, payments refunded

    Never a partial state: no group stuck in 'collecting', no paid share without a booking.

    This race condition was broken without `FOR UPDATE` — the payment thread would read the group status, the expiry job would expire it, and the share would remain 'paid' without a refund.
    """
    import random
    import threading
    import time
    from datetime import timedelta

    from sqlalchemy import select as sa_select, update as sa_update

    from database import SessionLocal
    from groups import expire_due_groups
    from models import GroupBooking, GroupShare, utcnow

    group = _make_group(client, tokens[0], group_seats[:2], minutes=5)
    st = group["share_token"]

    client.post(f"/api/groups/{st}/shares/{group['shares'][1]['id']}/claim",
                headers=_headers(tokens[1]))

    _pay_share(client, tokens[0], st, group["shares"][0]["id"])

    # Checkout for the final share created, settlement pending.
    res = client.post(f"/api/groups/{st}/shares/{group['shares'][1]['id']}/pay",
                      headers=_headers(tokens[1]))
    assert res.status_code == 200
    payment_id = res.json()["payment_id"]

    db = SessionLocal()
    try:
        db.execute(
            sa_update(GroupBooking)
            .where(GroupBooking.share_token == st)
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        db.commit()
    finally:
        db.close()

    barrier = threading.Barrier(2)

    def settle():
        barrier.wait()
        client.post(f"/api/payments/{payment_id}/simulate",
                    json={"outcome": "success"}, headers=_headers(tokens[1]))

    def expire():
        barrier.wait()
        # Jitter — without this, expiry always wins (direct function call vs full HTTP stack), and the other path is never tested.
        time.sleep(random.uniform(0, 0.12))
        d = SessionLocal()
        try:
            expire_due_groups(d)
        finally:
            d.close()

    t1, t2 = threading.Thread(target=settle), threading.Thread(target=expire)
    t1.start(); t2.start(); t1.join(); t2.join()

    db = SessionLocal()
    try:
        g = db.scalar(sa_select(GroupBooking).where(GroupBooking.share_token == st))
        shares = db.scalars(
            sa_select(GroupShare).where(GroupShare.group_id == g.id)
        ).all()

        assert g.status in ("confirmed", "expired"), \
            f"Group stuck in '{g.status}' — no winner."

        seat_states = [_seat(client, s.seat_id)["status"] for s in shares]

        if g.status == "confirmed":
            assert all(x == "booked" for x in seat_states), seat_states
            assert all(s.booking_id is not None for s in shares)
        else:
            assert all(x == "available" for x in seat_states), seat_states
            assert all(s.booking_id is None for s in shares)
            # ⭐ Payments already received must be refunded.
            for s in shares:
                assert s.status in ("refunded", "unpaid"), \
                    f"Share '{s.status}' in expired group — payment is stuck."
    finally:
        db.close()


def test_broken_group_does_not_leave_pending_payments(client, tokens, group_seats):
    """
    If a group is cancelled, its PENDING payments must also be closed.

    Otherwise, two issues arise:
      1. `uq_one_pending_payment_per_seat` prevents new checkouts for that seat — it appears 'available' but cannot be purchased.
      2. A user could complete an old checkout and pay for a defunct group.

    This was a real bug discovered while writing race condition tests.
    """
    from sqlalchemy import select as sa_select

    from database import SessionLocal
    from models import Payment

    group = _make_group(client, tokens[0], group_seats)
    st = group["share_token"]

    res = client.post(f"/api/groups/{st}/shares/{group['shares'][0]['id']}/pay",
                      headers=_headers(tokens[0]))
    assert res.status_code == 200

    client.delete(f"/api/groups/{st}", headers=_headers(tokens[0]))

    db = SessionLocal()
    try:
        still_pending = db.scalars(
            sa_select(Payment).where(
                Payment.seat_id.in_(group_seats), Payment.status == "pending"
            )
        ).all()
        assert not still_pending, f"{len(still_pending)} pending payments remain stuck"
    finally:
        db.close()

    # Now the same seat can be purchased normally — this is the actual check.
    # Previously, this returned 409 because the old pending payment index blocked it.
    res = client.post("/api/payments/checkout",
                      json={"seat_id": group_seats[0]}, headers=_headers(tokens[3]))
    assert res.status_code == 201, res.text

    # Do not leave pending payments behind — otherwise, the next test will collide with this index. (The same error we are currently testing.)
    client.post(f"/api/payments/{res.json()['payment_id']}/simulate",
                json={"outcome": "fail"}, headers=_headers(tokens[3]))


# ---------------------------------------------------------------------------
# Phase 18 — Seat layout
#
# Two parts:
#   1. validate/expand — pure functions, no DB access.
#   2. HTTP flow — both paths (layout and price_tiers) lead to the same destination.
#
# Most important invariant: **existing events (layout NULL) must not break.**
# ---------------------------------------------------------------------------

import layout as seat_layout


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
        headers=_headers(token),
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
        f"/api/organizer/events/{event['id']}", headers=_headers(token)
    ).status_code == 204


def test_bad_layout_creates_no_event(client, role_tokens):
    """
    ⭐ No seats (or events) should be created with an invalid layout.

    Validation runs BEFORE expansion, so the DB remains untouched. A partially created event is the worst-case scenario.
    """
    token = role_tokens["organizer"]
    before = len(client.get("/api/organizer/events", headers=_headers(token)).json())

    res = client.post(
        "/api/organizer/events",
        headers=_headers(token),
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

    after = len(client.get("/api/organizer/events", headers=_headers(token)).json())
    assert after == before, "event was created even though it should have failed"


def test_price_tiers_path_still_works_and_stores_a_layout(client, role_tokens):
    """
    Backwards compatibility — the legacy request body must work exactly as it did in Phase 10.
    """
    token = role_tokens["organizer"]
    res = client.post(
        "/api/organizer/events",
        headers=_headers(token),
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

    client.delete(f"/api/organizer/events/{event['id']}", headers=_headers(token))


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


# ---------------------------------------------------------------------------
# Phase 19 — Seat search
#
# ⭐ NONE of these tests require Gemini.
#
# This is intentional. The LLM only performs "text -> filters"; the entire search process thereafter is standard code. If these tests required an API key, they would be skipped in CI — and skipped tests appear green (a mistake caught in Phase 16).
# ---------------------------------------------------------------------------

import seat_search


class _FakeSeat:
    """A minimal seat for testing — no need to create a full ORM object."""

    def __init__(self, id, row, num, price=1000, status="available", section=None):
        self.id = id
        self.row_label = row
        self.seat_number = num
        self.price = price
        self.status = status
        self.section = section


# ⚠️ Named `_seat_row`, not `_row` — the Phase 18 layout tests already
# define a `_row()` helper with a different signature. Both live in this
# module, so reusing the name would silently shadow the first definition
# and make 8 existing tests fail with TypeError.
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


# ---- HTTP flow (AI ke bina) ----

def test_search_endpoint_works_without_ai(client, tokens):
    """
    ⭐ Search must work with filters even without AI.

    This is the most critical invariant of the feature: AI is an addition, not a dependency. If the key is missing, the model is down, or the quota is exhausted — search must still function.
    """
    res = client.post(
        "/api/events/1/seats/search",
        headers=_headers(tokens[0]),
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
        headers=_headers(tokens[0]),
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
        headers=_headers(tokens[0]),
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
        headers=_headers(tokens[0]),
        json={"filters": {"quantity": 9999}},
    )
    assert res.status_code == 422

    res = client.post(
        "/api/events/1/seats/search",
        headers=_headers(tokens[0]),
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
        headers=_headers(role_tokens["attendee"]),
        json={"brief": "some music event in mumbai"},
    )
    assert res.status_code == 403


def test_draft_needs_auth(client):
    res = client.post(
        "/api/organizer/events/draft", json={"brief": "some music event in mumbai"}
    )
    assert res.status_code == 401


def test_draft_rejects_empty_or_huge_briefs(client, role_tokens):
    token = role_tokens["organizer"]

    assert client.post(
        "/api/organizer/events/draft", headers=_headers(token), json={"brief": "hi"}
    ).status_code == 422

    assert client.post(
        "/api/organizer/events/draft",
        headers=_headers(token),
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
        headers=_headers(role_tokens["organizer"]),
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
    before = len(client.get("/api/organizer/events", headers=_headers(token)).json())

    client.post(
        "/api/organizer/events/draft",
        headers=_headers(token),
        json={"brief": "Some test event at a test venue in December"},
    )

    after = len(client.get("/api/organizer/events", headers=_headers(token)).json())
    assert after == before, "draft created an event — this should never happen"
