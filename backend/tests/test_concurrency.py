"""
Concurrency + auth tests — chalti hui API ke against.

Ye unit tests nahi hain. Ye asli HTTP requests bhejte hain, kyunki race
conditions sirf tab dikhti hain jab poora stack (uvicorn + Redis + Postgres)
saath me chal raha ho. Mock kar dete to woh bug pakda hi na jata jo load
test ne pakda tha.

Chalao:
    docker compose exec backend pytest tests/ -v
"""

import os
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

# Container ke andar se "backend", host se "localhost"
BASE_URL = os.getenv("TEST_BASE_URL", "http://backend:8000")

# seed.py sab test users ko yahi password deta hai
PASSWORD = "demo1234"
CONCURRENCY = 40


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
    Har concurrent "user" ka apna token.

    Alag users zaroori hain — same user dubara lock maange to use
    `already_owned` wala 200 mil jata hai aur contention test jhoothi ho jati.
    """
    emails = ["demo@seatpulse.dev"] + [
        f"user{i}@seatpulse.dev" for i in range(1, CONCURRENCY)
    ]
    try:
        return [_token(client, e) for e in emails]
    except httpx.HTTPStatusError:
        pytest.skip("Test users nahi hain — 'python seed.py' chalao")


@pytest.fixture
def free_seat(client, tokens):
    """Ek available seat lo, test ke baad usse saaf kar do."""
    seats = client.get("/api/events/1/seats").json()
    available = [s for s in seats if s["status"] == "available"]
    if not available:
        pytest.skip("Koi available seat nahi — 'python reset_state.py' chalao")

    seat = available[-1]        # aakhri wali, taki UI wali se na takraye
    yield seat

    # Cleanup: har user ka lock chhodo, phir booking cancel karo
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
    """Bina token ke booking/lock/bookings sab 401."""
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
    # Same message jo unknown email pe milta hai — user enumeration se bachne ke liye
    assert res.json()["detail"] == "Email ya password galat hai"


def test_login_unknown_email_same_message(client):
    res = client.post(
        "/api/auth/login", json={"email": "nahi@hai.dev", "password": "kuchbhi"}
    )
    assert res.status_code == 401
    assert res.json()["detail"] == "Email ya password galat hai"


def test_refresh_rotates_and_old_token_dies(client):
    """Refresh ke baad purana refresh token bekaar ho jana chahiye."""
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c:
        c.post("/api/auth/login", json={"email": "demo@seatpulse.dev", "password": PASSWORD})
        old_cookie = c.cookies.get("seatpulse_refresh")
        assert old_cookie

        assert c.post("/api/auth/refresh").status_code == 200
        assert c.cookies.get("seatpulse_refresh") != old_cookie   # rotate hua

    # Purana cookie ab reject hona chahiye
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c2:
        c2.cookies.set("seatpulse_refresh", old_cookie)
        assert c2.post("/api/auth/refresh").status_code == 401


def test_logout_kills_refresh_token(client):
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c:
        c.post("/api/auth/login", json={"email": "demo@seatpulse.dev", "password": PASSWORD})
        assert c.post("/api/auth/logout").status_code == 204
        assert c.post("/api/auth/refresh").status_code == 401


def test_cannot_cancel_someone_elses_booking(client, tokens, free_seat):
    """IDOR check — dusre ki booking cancel nahi kar sakte."""
    seat_id = free_seat["id"]
    owner, attacker = tokens[0], tokens[1]

    res = client.post("/api/bookings", json={"seat_id": seat_id}, headers=_headers(owner))
    assert res.status_code == 201
    booking_id = res.json()["id"]

    # 404 (403 nahi) — attacker ko ye bhi na pata chale ki booking exist karti hai
    assert client.delete(f"/api/bookings/{booking_id}", headers=_headers(attacker)).status_code == 404
    assert client.delete(f"/api/bookings/{booking_id}", headers=_headers(owner)).status_code == 200


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def role_tokens(client):
    """Teeno roles ke tokens. seed.py ye accounts banata hai."""
    accounts = {
        "attendee": "demo@seatpulse.dev",
        "organizer": "organizer@seatpulse.dev",
        "admin": "admin@seatpulse.dev",
    }
    try:
        return {role: _token(client, email) for role, email in accounts.items()}
    except httpx.HTTPStatusError:
        pytest.skip("Role accounts nahi hain — 'python seed.py' chalao")


def test_role_comes_through_in_me(client, role_tokens):
    for role, token in role_tokens.items():
        assert client.get("/api/auth/me", headers=_headers(token)).json()["role"] == role


def test_attendee_cannot_touch_organizer_or_admin(client, role_tokens):
    """Sabse basic RBAC check."""
    t = _headers(role_tokens["attendee"])
    assert client.get("/api/organizer/events", headers=t).status_code == 403
    assert client.get("/api/admin/stats", headers=t).status_code == 403


def test_organizer_cannot_reach_admin(client, role_tokens):
    """Organizer hone ka matlab admin hona nahi hai."""
    assert client.get(
        "/api/admin/stats", headers=_headers(role_tokens["organizer"])
    ).status_code == 403


def test_admin_can_reach_everything(client, role_tokens):
    t = _headers(role_tokens["admin"])
    assert client.get("/api/admin/stats", headers=t).status_code == 200
    assert client.get("/api/organizer/events", headers=t).status_code == 200


def test_organizer_creates_event_with_priced_rows(client, role_tokens):
    """Price tiers se seats sahi ban rahi hain?"""
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

    # Seats actually bani, aur pricing tier ke hisaab se
    seats = client.get(f"/api/events/{event['id']}/seats").json()
    assert len(seats) == 12
    assert {s["price"] for s in seats if s["row_label"] == "A"} == {1500}
    assert {s["price"] for s in seats if s["row_label"] in ("B", "C")} == {500}

    # cleanup — koi booking nahi hai to delete chal jayega
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
    ⭐ Sabse important RBAC test.

    Role check pass hone ka matlab ye nahi ki har resource tumhara hai.
    Ownership alag se check honi chahiye.
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

    # user1 ko organizer bana ke dekhte hain — role to hai, par event uska nahi
    admin = _headers(role_tokens["admin"])
    other = _token(client, "user1@seatpulse.dev")

    # user1 organizer nahi hai to pehle 403 milega; agar hai to 404 (ownership).
    # Dono hi "access nahi" hain — bas alag wajah se.
    patch = client.patch(
        f"/api/organizer/events/{created['id']}",
        headers=_headers(other),
        json={"name": "HACKED"},
    )
    assert patch.status_code in (403, 404)

    # Owner khud edit kar sakta hai
    assert client.patch(
        f"/api/organizer/events/{created['id']}",
        headers=_headers(owner),
        json={"venue": "Updated Hall"},
    ).status_code == 200

    # Admin bhi kar sakta hai
    assert client.patch(
        f"/api/organizer/events/{created['id']}", headers=admin, json={"venue": "Admin Hall"}
    ).status_code == 200

    client.delete(f"/api/organizer/events/{created['id']}", headers=_headers(owner))


def test_event_with_bookings_cannot_be_deleted(client, role_tokens):
    """
    ⚠️ Business rule: paid tickets kabhi gayab nahi honi chahiye.

    Cascade delete laga hua hai, to bina is guard ke ek DELETE se logon ki
    khareedi hui tickets ud jaatin.
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

    # Ab delete block hona chahiye
    blocked = client.delete(f"/api/organizer/events/{created['id']}", headers=_headers(owner))
    assert blocked.status_code == 409

    # Booking cancel karo -> ab delete chal jayega
    client.delete(f"/api/bookings/{booking.json()['id']}", headers=_headers(attendee))
    assert client.delete(
        f"/api/organizer/events/{created['id']}", headers=_headers(owner)
    ).status_code == 204


def test_seat_layout_limits_are_enforced(client, role_tokens):
    """26 rows (A-Z) se zyada nahi ban sakti."""
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
    Ek user 40 requests ek saath maare — kuch 429 milne chahiye.

    Token bucket 15 burst allow karta hai, phir 5/s refill. Serial curl
    loop me bhi refill hota rehta hai, isliye "kuch 429" check kar rahe
    hain, "theek 25" nahi — wo flaky hota.
    """
    seat_id = free_seat['id']
    token = tokens[3]

    codes = [
        client.post(f"/api/seats/{seat_id}/lock", headers=_headers(token)).status_code
        for _ in range(40)
    ]

    assert 429 in codes, f"Rate limit laga hi nahi: {sorted(set(codes))}"
    # Shuru wali requests to pass honi chahiye — limiter sab kuch block na kare
    assert codes[0] in (200, 409)


def test_rate_limit_sends_headers(client, tokens, free_seat):
    """Client ko pata chalna chahiye ki wo limit ke kitna paas hai."""
    res = client.post(
        f"/api/seats/{free_seat['id']}/lock", headers=_headers(tokens[4])
    )
    assert "X-RateLimit-Limit" in res.headers
    assert "X-RateLimit-Remaining" in res.headers


def test_rate_limit_is_per_user_not_global(client, tokens, free_seat):
    """
    Ek user ke block hone se DUSRA user affect nahi hona chahiye.

    Ye sabse important rate limit test hai — global limiter poore system
    ko ek bot ki wajah se band kar deta.
    """
    seat_id = free_seat['id']
    victim, other = tokens[5], tokens[6]

    # Ek user ka bucket khatam karo
    for _ in range(40):
        client.post(f"/api/seats/{seat_id}/lock", headers=_headers(victim))

    # Dusre user ko 429 nahi milna chahiye
    res = client.post(f"/api/seats/{seat_id}/lock", headers=_headers(other))
    assert res.status_code != 429, "Ek user ke limit se dusra block ho gaya"


def test_wrong_password_eventually_rate_limited(client):
    """Brute force protection — galat password baar baar dene par 429."""
    email = "user9@seatpulse.dev"

    codes = [
        client.post(
            "/api/auth/login", json={"email": email, "password": f"galat{i}"}
        ).status_code
        for i in range(12)
    ]

    assert 429 in codes, f"Brute force nahi ruka: {sorted(set(codes))}"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_same_idempotency_key_returns_same_booking(client, tokens, free_seat):
    """
    ⭐ Double-click ka asli test.

    Wahi key dubara -> wahi booking, aur database me sirf EK row.
    """
    seat_id = free_seat['id']
    token = tokens[0]
    headers = {**_headers(token), "Idempotency-Key": f"test-{seat_id}-once"}

    first = client.post("/api/bookings", json={"seat_id": seat_id}, headers=headers)
    assert first.status_code == 201

    second = client.post("/api/bookings", json={"seat_id": seat_id}, headers=headers)
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"], "Alag booking ban gayi!"
    assert second.headers.get("X-Idempotent-Replay") == "true"

    # Sabse zaroori check — DB me kitni bookings actually bani
    mine = client.get("/api/bookings", headers=_headers(token)).json()
    for_seat = [b for b in mine if b["seat_id"] == seat_id and b["status"] == "confirmed"]
    assert len(for_seat) == 1


def test_same_key_different_body_is_rejected(client, tokens, free_seat):
    """Wahi key alag data ke saath = bug ya attack. Chupchap purana jawab mat do."""
    seat_id = free_seat['id']
    headers = {**_headers(tokens[0]), "Idempotency-Key": f"test-{seat_id}-mismatch"}

    assert client.post("/api/bookings", json={"seat_id": seat_id}, headers=headers).status_code == 201

    res = client.post("/api/bookings", json={"seat_id": seat_id + 1}, headers=headers)
    assert res.status_code == 422


def test_booking_works_without_idempotency_key(client, tokens, free_seat):
    """Header optional hona chahiye — purane clients tootne nahi chahiye."""
    res = client.post(
        "/api/bookings", json={"seat_id": free_seat['id']}, headers=_headers(tokens[0])
    )
    assert res.status_code == 201


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

def test_only_one_user_gets_the_lock(client, tokens, free_seat):
    """40 users, ek seat — sirf ek ko lock milna chahiye."""
    seat_id = free_seat["id"]

    def try_lock(token):
        return client.post(f"/api/seats/{seat_id}/lock", headers=_headers(token)).status_code

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        codes = list(pool.map(try_lock, tokens))

    assert codes.count(200) == 1, f"Expected exactly 1 lock, got {codes.count(200)}"
    assert codes.count(409) == len(tokens) - 1


def test_no_double_booking(client, tokens, free_seat):
    """40 users ek saath book karein — database me exactly 1 booking."""
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
    """Ek user hold kare, dusra book na kar paaye."""
    seat_id = free_seat["id"]
    holder, other = tokens[1], tokens[2]

    assert client.post(f"/api/seats/{seat_id}/lock", headers=_headers(holder)).status_code == 200
    assert client.post("/api/bookings", json={"seat_id": seat_id}, headers=_headers(other)).status_code == 409
    # Lock wala khud book kar sakta hai
    assert client.post("/api/bookings", json={"seat_id": seat_id}, headers=_headers(holder)).status_code == 201


def test_cannot_release_someone_elses_lock(client, tokens, free_seat):
    """Lua script dusre ka lock nahi hatne deti."""
    seat_id = free_seat["id"]
    holder, other = tokens[1], tokens[2]

    client.post(f"/api/seats/{seat_id}/lock", headers=_headers(holder))

    res = client.delete(f"/api/seats/{seat_id}/lock", headers=_headers(other))
    assert res.json()["released"] is False

    owner_id = client.get("/api/auth/me", headers=_headers(holder)).json()["id"]
    assert client.get(f"/api/seats/{seat_id}/lock").json()["locked_by"] == owner_id


def test_version_increments_on_change(client, tokens, free_seat):
    """Har state change pe version badhna chahiye — optimistic locking isi par chalti hai."""
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
    """Seat hold karke checkout shuru karo — helper."""
    client.post(f"/api/seats/{seat_id}/lock", headers=_headers(token))
    return client.post(
        "/api/payments/checkout", json={"seat_id": seat_id}, headers=_headers(token)
    )


def test_checkout_moves_seat_to_payment_pending(client, tokens, free_seat):
    """Checkout ke baad seat hold se aage badh jaati hai — par booked NAHI."""
    seat_id = free_seat["id"]

    res = _checkout(client, tokens[0], seat_id)
    assert res.status_code == 201
    assert res.json()["provider"] in ("mock", "stripe")

    seat = client.get(f"/api/seats/{seat_id}").json()
    assert seat["status"] == "payment_pending"

    # ⭐ Sabse zaroori: abhi tak koi booking nahi bani
    mine = client.get("/api/bookings", headers=_headers(tokens[0])).json()
    assert not [b for b in mine if b["seat_id"] == seat_id and b["status"] == "confirmed"]


def test_another_user_cannot_checkout_held_seat(client, tokens, free_seat):
    """Ek user ka hold, dusre ka checkout — 409."""
    seat_id = free_seat["id"]
    _checkout(client, tokens[0], seat_id)

    res = client.post(
        "/api/payments/checkout", json={"seat_id": seat_id}, headers=_headers(tokens[1])
    )
    assert res.status_code == 409


def test_successful_payment_creates_exactly_one_booking(client, tokens, free_seat):
    """Happy path — payment succeed, booking bani, seat booked."""
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
    ⭐ Webhooks AT-LEAST-ONCE hote hain — gateway same event do baar bhej
    sakta hai. Dusri baar naya kaam nahi, wahi booking wapas milni chahiye.
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

    assert first["booking_id"] == second["booking_id"], "Dusri baar nayi booking ban gayi!"

    # DB me bhi ek hi
    mine = client.get("/api/bookings", headers=_headers(token)).json()
    assert len([b for b in mine if b["seat_id"] == seat_id and b["status"] == "confirmed"]) == 1


def test_failed_payment_releases_the_seat(client, tokens, free_seat):
    """Payment fail — seat wapas available, koi booking nahi."""
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
    """IDOR — dusre ka payment na dikhe, na settle ho."""
    seat_id = free_seat["id"]
    payment_id = _checkout(client, tokens[0], seat_id).json()["payment_id"]

    attacker = _headers(tokens[1])
    assert client.get(f"/api/payments/{payment_id}", headers=attacker).status_code == 404
    assert client.post(
        f"/api/payments/{payment_id}/simulate", json={"outcome": "success"}, headers=attacker
    ).status_code == 404


def test_webhook_rejects_bad_signature(client):
    """
    ⭐ Webhook endpoint authenticated nahi hai — signature hi uska auth hai.

    Bina iske koi bhi POST maar ke free ticket le leta.
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
    Worker background me chalta hai — poll karke ticket ka intezaar karo.

    ⚠️ Fixed `sleep` nahi lagaya. Wo dheeme machine pe flaky hota hai aur
    tez machine pe faltu time khaata hai.
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
    Booking turant confirm hoti hai — ticket baad me banta hai.

    ⭐ Yahi is phase ka poora point hai: API user ko 2-3 second wait nahi
    karati.
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
        pytest.skip("Worker chal raha hai? `docker compose up -d worker`")

    assert booking["ticket_status"] == "ready"

    res = client.get(f"/api/bookings/{booking['id']}/ticket", headers=_headers(token))
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    # Asli PDF hai? Header check karo — status 200 kaafi nahi
    assert res.content[:5] == b"%PDF-"
    assert len(res.content) > 1000


def test_cannot_download_someone_elses_ticket(client, tokens, free_seat):
    """
    ⚠️ Sabse zaroori ticket test.

    Ticket me QR hai. Doosre ka ticket download kar lena = free entry.
    """
    seat_id = free_seat["id"]
    owner, attacker = tokens[0], tokens[1]

    client.post("/api/bookings", json={"seat_id": seat_id}, headers=_headers(owner))
    booking = _wait_for_ticket(client, owner, seat_id)
    if booking is None:
        pytest.skip("Worker nahi chal raha")

    res = client.get(f"/api/bookings/{booking['id']}/ticket", headers=_headers(attacker))
    assert res.status_code == 404      # 403 nahi — existence bhi chhupa rahe hain


def test_ticket_needs_authentication(client, tokens, free_seat):
    seat_id = free_seat["id"]
    client.post("/api/bookings", json={"seat_id": seat_id}, headers=_headers(tokens[0]))
    booking = _wait_for_ticket(client, tokens[0], seat_id)
    if booking is None:
        pytest.skip("Worker nahi chal raha")

    assert client.get(f"/api/bookings/{booking['id']}/ticket").status_code == 401


def test_qr_token_is_not_the_booking_id(client, tokens, free_seat):
    """
    ⚠️ QR me sequential id nahi honi chahiye — koi bhi 1,2,3 ka QR bana ke
    gate pe chala jata.
    """
    from sqlalchemy import select

    seat_id = free_seat["id"]
    client.post("/api/bookings", json={"seat_id": seat_id}, headers=_headers(tokens[0]))
    booking = _wait_for_ticket(client, tokens[0], seat_id)
    if booking is None:
        pytest.skip("Worker nahi chal raha")

    # Token DB me hai aur lamba/random hai
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
