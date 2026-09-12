"""
The overselling defences: seat locks, rate limits, idempotency keys, and both
locking strategies.

Every test asserts an invariant: one seat, one booking, by firing real
concurrent requests at the running stack. A mocked database cannot reproduce
these races.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor

from helpers import CONCURRENCY, auth_headers

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
        client.post(f"/api/seats/{seat_id}/lock", headers=auth_headers(token)).status_code
        for _ in range(40)
    ]

    assert 429 in codes, f"Rate limit not applied: {sorted(set(codes))}"
    # Initial requests should pass — the limiter shouldn't block everything
    assert codes[0] in (200, 409)


def test_rate_limit_sends_headers(client, tokens, free_seat):
    """Client should know how close it is to the limit."""
    res = client.post(
        f"/api/seats/{free_seat['id']}/lock", headers=auth_headers(tokens[4])
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
        client.post(f"/api/seats/{seat_id}/lock", headers=auth_headers(victim))

    # The other user should not receive a 429
    res = client.post(f"/api/seats/{seat_id}/lock", headers=auth_headers(other))
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
    headers = {**auth_headers(token), "Idempotency-Key": f"test-{seat_id}-once-{RUN_ID}"}

    first = client.post("/api/bookings", json={"seat_id": seat_id}, headers=headers)
    assert first.status_code == 201

    second = client.post("/api/bookings", json={"seat_id": seat_id}, headers=headers)
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"], "A different booking was created!"
    assert second.headers.get("X-Idempotent-Replay") == "true"

    # Most important check — how many bookings were actually created in the DB
    mine = client.get("/api/bookings", headers=auth_headers(token)).json()
    for_seat = [b for b in mine if b["seat_id"] == seat_id and b["status"] == "confirmed"]
    assert len(for_seat) == 1


def test_same_key_different_body_is_rejected(client, tokens, free_seat):
    """Same key with different data = bug or attack. Do not silently return the old response."""
    seat_id = free_seat['id']
    headers = {**auth_headers(tokens[0]), "Idempotency-Key": f"test-{seat_id}-mismatch-{RUN_ID}"}

    assert client.post("/api/bookings", json={"seat_id": seat_id}, headers=headers).status_code == 201

    res = client.post("/api/bookings", json={"seat_id": seat_id + 1}, headers=headers)
    assert res.status_code == 422


def test_booking_works_without_idempotency_key(client, tokens, free_seat):
    """Header should be optional — legacy clients should not break."""
    res = client.post(
        "/api/bookings", json={"seat_id": free_seat['id']}, headers=auth_headers(tokens[0])
    )
    assert res.status_code == 201


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

def test_only_one_user_gets_the_lock(client, tokens, free_seat):
    """40 users, one seat — only one should get the lock."""
    seat_id = free_seat["id"]

    def try_lock(token):
        return client.post(f"/api/seats/{seat_id}/lock", headers=auth_headers(token)).status_code

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        codes = list(pool.map(try_lock, tokens))

    assert codes.count(200) == 1, f"Expected exactly 1 lock, got {codes.count(200)}"
    assert codes.count(409) == len(tokens) - 1


def test_no_double_booking(client, tokens, free_seat):
    """40 users booking simultaneously — exactly 1 booking in the database."""
    seat_id = free_seat["id"]

    def try_book(token):
        return client.post(
            "/api/bookings", json={"seat_id": seat_id}, headers=auth_headers(token)
        ).status_code

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        codes = list(pool.map(try_book, tokens))

    assert codes.count(201) == 1, f"Expected exactly 1 booking, got {codes.count(201)}"
    assert client.get(f"/api/seats/{seat_id}").json()["status"] == "booked"


def test_lock_blocks_other_users_booking(client, tokens, free_seat):
    """If one user holds a seat, another cannot book it."""
    seat_id = free_seat["id"]
    holder, other = tokens[1], tokens[2]

    assert client.post(f"/api/seats/{seat_id}/lock", headers=auth_headers(holder)).status_code == 200
    assert client.post("/api/bookings", json={"seat_id": seat_id}, headers=auth_headers(other)).status_code == 409
    # The lock holder can book the seat
    assert client.post("/api/bookings", json={"seat_id": seat_id}, headers=auth_headers(holder)).status_code == 201


def test_cannot_release_someone_elses_lock(client, tokens, free_seat):
    """Lua script prevents unauthorized lock release."""
    seat_id = free_seat["id"]
    holder, other = tokens[1], tokens[2]

    client.post(f"/api/seats/{seat_id}/lock", headers=auth_headers(holder))

    res = client.delete(f"/api/seats/{seat_id}/lock", headers=auth_headers(other))
    assert res.json()["released"] is False

    owner_id = client.get("/api/auth/me", headers=auth_headers(holder)).json()["id"]
    assert client.get(f"/api/seats/{seat_id}/lock").json()["locked_by"] == owner_id


def test_version_increments_on_change(client, tokens, free_seat):
    """Version must increment on state change — required for optimistic locking."""
    seat_id = free_seat["id"]
    token = tokens[1]

    before = client.get(f"/api/seats/{seat_id}").json()["version"]

    client.post(f"/api/seats/{seat_id}/lock", headers=auth_headers(token))
    after_lock = client.get(f"/api/seats/{seat_id}").json()["version"]
    assert after_lock > before

    client.post("/api/bookings", json={"seat_id": seat_id}, headers=auth_headers(token))
    assert client.get(f"/api/seats/{seat_id}").json()["version"] > after_lock


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
            headers=auth_headers(token),
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
        headers=auth_headers(tokens[0]),
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
            headers=auth_headers(token),
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
            headers=auth_headers(tokens[0]),
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
        for b in client.get("/api/bookings", headers=auth_headers(tokens[0])).json():
            if b["event_id"] == ev["id"]:
                client.delete(f"/api/bookings/{b['id']}", headers=auth_headers(tokens[0]))
        client.delete(f"/api/organizer/events/{ev['id']}", headers=auth_headers(token))

    assert states[0] == states[1], f"strategies left different states: {states}"
    assert states[0]["status"] == "booked"
    assert states[0]["version_delta"] == 1
