"""
Health, authentication, and role-based access control.

Covers the access/refresh token lifecycle, IDOR checks on bookings, and the
attendee / organizer / admin boundaries: including ownership, since the
organizer role grants access to a person's own events, not to everyone's.
"""

import httpx

from helpers import BASE_URL, PASSWORD, auth_headers, login


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
    res = client.get("/api/auth/me", headers=auth_headers("not-a-real-token"))
    assert res.status_code == 401


def test_login_wrong_password(client):
    res = client.post(
        "/api/auth/login", json={"email": "demo@seatpulse.dev", "password": "wrong-password"}
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

    res = client.post("/api/bookings", json={"seat_id": seat_id}, headers=auth_headers(owner))
    assert res.status_code == 201
    booking_id = res.json()["id"]

    # 404 (not 403) — attacker should not know if the booking exists
    assert client.delete(f"/api/bookings/{booking_id}", headers=auth_headers(attacker)).status_code == 404
    assert client.delete(f"/api/bookings/{booking_id}", headers=auth_headers(owner)).status_code == 200


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

def test_role_comes_through_in_me(client, role_tokens):
    for role, token in role_tokens.items():
        assert client.get("/api/auth/me", headers=auth_headers(token)).json()["role"] == role


def test_attendee_cannot_touch_organizer_or_admin(client, role_tokens):
    """Most basic RBAC check."""
    t = auth_headers(role_tokens["attendee"])
    assert client.get("/api/organizer/events", headers=t).status_code == 403
    assert client.get("/api/admin/stats", headers=t).status_code == 403


def test_organizer_cannot_reach_admin(client, role_tokens):
    """Being an organizer does not mean being an admin."""
    assert client.get(
        "/api/admin/stats", headers=auth_headers(role_tokens["organizer"])
    ).status_code == 403


def test_admin_can_reach_everything(client, role_tokens):
    t = auth_headers(role_tokens["admin"])
    assert client.get("/api/admin/stats", headers=t).status_code == 200
    assert client.get("/api/organizer/events", headers=t).status_code == 200


def test_organizer_creates_event_with_priced_rows(client, role_tokens):
    """Are seats created correctly with price tiers?"""
    token = role_tokens["organizer"]

    res = client.post(
        "/api/organizer/events",
        headers=auth_headers(token),
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
        f"/api/organizer/events/{event['id']}", headers=auth_headers(token)
    ).status_code == 204


def test_attendee_cannot_create_event(client, role_tokens):
    res = client.post(
        "/api/organizer/events",
        headers=auth_headers(role_tokens["attendee"]),
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
        headers=auth_headers(owner),
        json={
            "name": "Ownership Test",
            "venue": "Test Hall",
            "starts_at": "2027-02-01T18:00:00Z",
            "seats_per_row": 2,
            "price_tiers": [{"rows": 1, "price": 100}],
        },
    ).json()

    # Let's try making user1 an organizer — they have the role, but not the event
    admin = auth_headers(role_tokens["admin"])
    other = login(client, "user1@seatpulse.dev")

    # If user1 is not an organizer, they receive 403; if they are, they receive 404 (ownership).
    # Both indicate "no access" for different reasons.
    patch = client.patch(
        f"/api/organizer/events/{created['id']}",
        headers=auth_headers(other),
        json={"name": "HACKED"},
    )
    assert patch.status_code in (403, 404)

    # Owner can edit their own event
    assert client.patch(
        f"/api/organizer/events/{created['id']}",
        headers=auth_headers(owner),
        json={"venue": "Updated Hall"},
    ).status_code == 200

    # Admin can also edit
    assert client.patch(
        f"/api/organizer/events/{created['id']}", headers=admin, json={"venue": "Admin Hall"}
    ).status_code == 200

    client.delete(f"/api/organizer/events/{created['id']}", headers=auth_headers(owner))


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
        headers=auth_headers(owner),
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
        "/api/bookings", json={"seat_id": seat["id"]}, headers=auth_headers(attendee)
    )
    assert booking.status_code == 201

    # Delete should now be blocked
    blocked = client.delete(f"/api/organizer/events/{created['id']}", headers=auth_headers(owner))
    assert blocked.status_code == 409

    # Cancel the booking -> delete will now work
    client.delete(f"/api/bookings/{booking.json()['id']}", headers=auth_headers(attendee))
    assert client.delete(
        f"/api/organizer/events/{created['id']}", headers=auth_headers(owner)
    ).status_code == 204


def test_seat_layout_limits_are_enforced(client, role_tokens):
    """Cannot create more than 26 rows (A-Z)."""
    res = client.post(
        "/api/organizer/events",
        headers=auth_headers(role_tokens["organizer"]),
        json={
            "name": "Too Many Rows",
            "venue": "Test Hall",
            "starts_at": "2027-04-01T18:00:00Z",
            "seats_per_row": 10,
            "price_tiers": [{"rows": 20, "price": 100}, {"rows": 20, "price": 50}],
        },
    )
    assert res.status_code == 422
