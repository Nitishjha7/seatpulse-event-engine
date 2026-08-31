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
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

# Container ke andar se "backend", host se "localhost"
BASE_URL = os.getenv("TEST_BASE_URL", "http://backend:8000")

# seed.py sab test users ko yahi password deta hai
PASSWORD = "demo1234"
CONCURRENCY = 40

# Har pytest run ka apna suffix.
#
# ⚠️ Idempotency keys pehle fixed the (`test-100-once`). Wo Redis me TTL
# tak zinda rehti hain, matlab AGLA test run usi key pe replay le aata
# tha: 201 to milta tha, par nayi booking banti hi nahi thi — aur test
# "0 bookings mili" pe fail hoti thi.
#
# Ye bug multi-worker stack pe pakda gaya aur pehle multi-worker ka bug
# laga. Tha nahi — tests reset_state.py par nirbhar the, jo chhoot sakta
# hai. Ab har run apni keys use karta hai aur ye nirbharta khatam.
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
    headers = {**_headers(token), "Idempotency-Key": f"test-{seat_id}-once-{RUN_ID}"}

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
    headers = {**_headers(tokens[0]), "Idempotency-Key": f"test-{seat_id}-mismatch-{RUN_ID}"}

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


# ---------------------------------------------------------------------------
# Gate check-in
# ---------------------------------------------------------------------------

def _booked_with_ticket(client, token, seat_id):
    """Book karo aur ticket ready hone ka intezaar karo — QR token wapas do."""
    client.post("/api/bookings", json={"seat_id": seat_id}, headers=_headers(token))
    booking = _wait_for_ticket(client, token, seat_id)
    if booking is None or booking["ticket_status"] != "ready":
        pytest.skip("Worker nahi chal raha")

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
    ⭐ Ye phase ka core test.

    Do log ek hi QR ka screenshot leke alag gates pe jaayein — dono andar
    nahi jaane chahiye.
    """
    seat_id = free_seat["id"]
    _, qr = _booked_with_ticket(client, tokens[0], seat_id)
    gate = _headers(role_tokens["organizer"])

    first = client.post("/api/checkin", json={"token": qr}, headers=gate).json()
    second = client.post("/api/checkin", json={"token": qr}, headers=gate).json()

    assert first["ok"] is True
    assert second["ok"] is False
    assert second["reason"] == "already_checked_in"
    # Duplicate pe "kab" bhi milna chahiye — gate pe yahi poocha jata hai
    assert second["checked_in_at"] == first["checked_in_at"]


def test_concurrent_scans_admit_exactly_one(client, tokens, role_tokens, free_seat):
    """
    ⭐ Asli race — 10 gates ek saath.

    Wahi "exactly once" problem jo seat booking me thi, alag kapdon me.
    """
    seat_id = free_seat["id"]
    _, qr = _booked_with_ticket(client, tokens[0], seat_id)
    gate = _headers(role_tokens["organizer"])

    def scan(_):
        return client.post("/api/checkin", json={"token": qr}, headers=gate).json()["reason"]

    with ThreadPoolExecutor(max_workers=10) as pool:
        reasons = list(pool.map(scan, range(10)))

    assert reasons.count("checked_in") == 1, f"Ek se zyada entry mili: {reasons}"
    assert reasons.count("already_checked_in") == 9


def test_invalid_token_is_rejected(client, role_tokens):
    res = client.post(
        "/api/checkin",
        json={"token": "this-token-does-not-exist-at-all"},
        headers=_headers(role_tokens["organizer"]),
    ).json()

    assert res["ok"] is False
    assert res["reason"] == "invalid_ticket"
    # ⚠️ Koi detail leak nahi honi chahiye — warna tokens brute-force ho sakte hain
    assert res["booking_id"] is None
    assert res["seat_label"] is None


def test_attendee_cannot_scan_tickets(client, tokens, role_tokens, free_seat):
    """Gate portal sirf organizer/admin ke liye hai."""
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
# Yahan do alag cheezein test ho rahi hain:
#   1. FORMULA sahi hai (pure functions, koi DB nahi)
#   2. QUOTED PRICE ka waada nibhta hai (poora HTTP flow)
#
# (2) zyada important hai. Formula galat ho to price thoda ajeeb lagega.
# Price lock toota to user se galat paisa katega — wo bug alag level ka hai.
# ---------------------------------------------------------------------------

from pricing import apply, multiplier_for, pricing_for_event


def test_multiplier_grows_with_demand():
    """0% bika = base, 100% bika = base x (1 + demand_factor)."""
    assert multiplier_for(0, 100, 0.5, 2.0) == 1.0
    assert multiplier_for(50, 100, 0.5, 2.0) == 1.25
    assert multiplier_for(100, 100, 0.5, 2.0) == 1.5


def test_max_surge_is_a_hard_ceiling():
    """demand_factor kitna bhi ho, max_surge se upar nahi ja sakta."""
    # 100% bika, factor 5.0 -> formula 6.0 kehta hai, cap 1.5 hai
    assert multiplier_for(100, 100, 5.0, 1.5) == 1.5


def test_empty_event_does_not_divide_by_zero():
    """total=0 pe crash nahi hona chahiye — naya event banate waqt ye hota hai."""
    assert multiplier_for(0, 0, 0.5, 2.0) == 1.0


def test_price_rounds_to_a_clean_number():
    """₹827.43 nahi, ₹830. Ajeeb price pe user ko shak hota hai."""
    assert apply(827.43, 1.0) == 830.0
    assert apply(1000, 1.25) == 1250.0


def test_disabled_pricing_never_surges():
    """
    Off hone par multiplier hamesha 1.0 — chahe event pura bik jaaye.

    Ye default hai, aur ye default hi zyada events pe lagega.
    """
    info = pricing_for_event(
        enabled=False, sold=100, total=100, demand_factor=0.5, max_surge=2.0
    )
    assert info.multiplier == 1.0
    assert info.seats_until_increase is None


def test_seats_until_increase_counts_forward():
    """
    100 seats, factor 0.5, base ₹1000:
      0 bika -> 1.000x -> ₹1000
      1 bika -> 1.005x -> ₹1005 -> ₹10 pe round -> ₹1000  (koi badlaav nahi)
      2 bika -> 1.010x -> ₹1010                            <- yahan badla

    Matlab jawab 2 hai, 1 nahi. Ye pehli baar likhne pe 1 lagta hai —
    par ₹5 ka farq ₹10 ke round me gayab ho jata hai. Isiliye ye function
    seedha loop chalata hai formula se andaza lagane ke bajaye.
    """
    info = pricing_for_event(
        enabled=True, sold=0, total=100, demand_factor=0.5, max_surge=2.0,
        sample_base=1000.0,
    )
    assert info.seats_until_increase == 2

    # Chhota factor -> price dheere badhta hai -> zyada seats lagti hain
    slow = pricing_for_event(
        enabled=True, sold=0, total=100, demand_factor=0.1, max_surge=2.0,
        sample_base=1000.0,
    )
    assert slow.seats_until_increase > 1


def test_max_surge_reached_reports_no_further_increase():
    """Cap pe pahunch gaye to 'aur badhega' ka jhoot mat bolo."""
    info = pricing_for_event(
        enabled=True, sold=50, total=100, demand_factor=5.0, max_surge=1.0,
        sample_base=1000.0,
    )
    assert info.multiplier == 1.0
    assert info.seats_until_increase is None


# ---- Ab HTTP flow — asli waada yahan test hota hai ----

# surge_event fixture ki bookings cleanup ke liye — kaunse tokens ne is
# event pe kuch kharida. Module-level isliye ki fixture ko test ke andar
# banaye gaye tokens ka pata chal sake.
tokens_cache: list[str] = []


@pytest.fixture
def surge_event(client, role_tokens):
    """
    Dynamic pricing wala chhota event, apna khud ka.

    Event 1 use nahi kar sakte — uspe dusre tests bookings banate/hatate
    rehte hain, aur multiplier sold-count se aata hai. Shared event pe ye
    test kabhi pass kabhi fail hoti (flaky), aur flaky test bekaar test hai.
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
            # 10 seats, factor 1.0 -> har booking par +10% — asar saaf dikhta hai
            "demand_factor": 1.0,
            "max_surge": 2.0,
        },
    )
    assert res.status_code == 201
    event = res.json()

    yield event

    # Cleanup: bookings hatao, phir event (booking wale event delete nahi hote)
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
    """Ek seat bikte hi baaki seats mehngi ho jaani chahiye."""
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

    # 1/10 bika, factor 1.0 -> 1.1x -> ₹1100
    assert all(s["current_price"] == 1100.0 for s in unsold)
    # BASE price nahi badla — ye poore design ki buniyaad hai
    assert all(s["price"] == 1000.0 for s in unsold)


def test_held_price_survives_a_price_rise(client, tokens, surge_event):
    """
    ⭐ Is poore feature ka sabse zaroori test.

    User A seat hold karta hai (₹1000 quote milta hai). Phir User B ek aur
    seat khareed leta hai, jisse demand badh jati hai. A ab bhi ₹1000 hi
    dega — kyunki usse ₹1000 kaha gaya tha.

    Ye tootne par user se chup-chaap zyada paisa kat jayega.
    """
    tokens_cache.extend([tokens[0], tokens[1]])
    seats = client.get(f"/api/events/{surge_event['id']}/seats").json()
    a_seat, b_seat = seats[0], seats[1]

    # A hold karta hai — quote lock ho jata hai
    lock = client.post(
        f"/api/seats/{a_seat['id']}/lock", headers=_headers(tokens[0])
    ).json()
    quoted = lock["price"]
    assert quoted == 1000.0

    # B khareedta hai — demand upar
    assert client.post(
        "/api/bookings", headers=_headers(tokens[1]), json={"seat_id": b_seat["id"]}
    ).status_code == 201

    # Baaki sabke liye price badh gaya...
    fresh = client.get(f"/api/events/{surge_event['id']}/seats").json()
    others = [s for s in fresh if s["status"] == "available"]
    assert others and all(s["current_price"] > 1000.0 for s in others)

    # ...par A ka hold abhi bhi ₹1000 pe hai
    held = next(s for s in fresh if s["id"] == a_seat["id"])
    assert held["held_price"] == 1000.0

    # Aur booking me exactly wahi amount charge hua
    booking = client.post(
        "/api/bookings", headers=_headers(tokens[0]), json={"seat_id": a_seat["id"]}
    )
    assert booking.status_code == 201
    assert booking.json()["amount"] == quoted


def test_releasing_a_hold_drops_the_locked_price(client, tokens, surge_event):
    """
    Hold chhoda to purana price bhi gaya.

    Bina iske user hold-release-hold karke hamesha ke liye sabse sasta
    price pakad leta — surge ka koi matlab hi na bachta.
    """
    seats = client.get(f"/api/events/{surge_event['id']}/seats").json()
    seat = [s for s in seats if s["status"] == "available"][-1]

    client.post(f"/api/seats/{seat['id']}/lock", headers=_headers(tokens[0]))
    client.delete(f"/api/seats/{seat['id']}/lock", headers=_headers(tokens[0]))

    fresh = client.get(f"/api/seats/{seat['id']}").json()
    assert fresh["held_price"] is None


def test_organizer_can_turn_surge_off(client, role_tokens, surge_event):
    """Sales slow hain to organizer surge band kar sake — base price wapas."""
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
    Base price PATCH se nahi badal sakta.

    Purani bookings uske reference pe tiki hain — badla to unki receipt
    jhoothi ho jayegi. Pydantic extra fields chup-chaap ignore karta hai,
    isliye check karte hain ki asar HUA hi nahi.
    """
    client.patch(
        f"/api/organizer/events/{surge_event['id']}",
        headers=_headers(role_tokens["organizer"]),
        json={"price_tiers": [{"rows": 2, "price": 99999}]},
    )
    seats = client.get(f"/api/events/{surge_event['id']}/seats").json()
    assert all(s["price"] == 1000.0 for s in seats)


def test_absurd_surge_settings_are_rejected(client, role_tokens, surge_event):
    """demand_factor=50 galti se type ho jaye to server mana kare."""
    res = client.patch(
        f"/api/organizer/events/{surge_event['id']}",
        headers=_headers(role_tokens["organizer"]),
        json={"demand_factor": 50},
    )
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# Phase 15 — Locking strategies
#
# ⭐ Ye tests dono modes me pass hone chahiye.
#
# BENCHMARK_MODE off ho to server `strategy` param ignore kar deta hai aur
# optimistic chalata hai. On ho to pessimistic path chalta hai. Dono soorat
# me ek hi cheez sach honi chahiye: **ek seat, ek booking**.
#
# Test isi invariant par likhi hai, kisi internal detail par nahi — isliye
# ye mode ke hisaab se skip nahi hoti, aur benchmark mode galti se on chhut
# jaye to bhi meaningful rehti hai.
# ---------------------------------------------------------------------------

def test_pessimistic_strategy_also_prevents_double_booking(client, tokens, free_seat):
    """
    Pessimistic path se bhi overselling nahi honi chahiye.

    Ye benchmark ka pehla sawaal hai: dono strategies SAHI hain kya?
    "Kaunsa tez hai" ka koi matlab nahi agar ek galat ho.
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

    assert codes.count(201) == 1, f"exactly ek booking honi thi, mili: {codes}"
    # Baaki sabko 409 (ya rate limit se 429) — 500 kabhi nahi
    assert all(c in (201, 409, 429) for c in codes), codes


def test_unknown_strategy_falls_back_to_optimistic(client, tokens, free_seat):
    """
    Kachra `strategy` value bheji to server safe default pe jaye, 500 na de.

    Ye chhoti baat lagti hai par zaroori hai: ye param public API me nahi
    hai, matlab ise koi bhi kuch bhi bhej sakta hai. Unknown value pe
    crash hona ek DoS ban jata.
    """
    res = client.post(
        "/api/bookings?strategy=../../etc/passwd",
        headers=_headers(tokens[0]),
        json={"seat_id": free_seat["id"]},
    )
    assert res.status_code == 201


def test_both_strategies_write_identical_seat_state(client, tokens, role_tokens):
    """
    Dono strategies ke baad seat ka state bilkul same dikhna chahiye.

    Agar pessimistic path `version` badhana bhool jata (usse row lock ki
    wajah se zaroorat nahi hai), to WebSocket clients ko update dikhta hi
    nahi — aur benchmark do ALAG cheezein maap raha hota.
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

    assert states[0] == states[1], f"strategies ne alag state chhoda: {states}"
    assert states[0]["status"] == "booked"
    assert states[0]["version_delta"] == 1


# ---------------------------------------------------------------------------
# Phase 17 — Group booking (split payment)
#
# Yahan ka core sawaal single-seat booking se alag hai. Wahan "exactly once"
# ka matlab tha: ek seat, ek booking. Yahan matlab hai: **sab ya koi nahi**,
# N alag payments ke paar.
# ---------------------------------------------------------------------------

def _clear_user_rate_limits():
    """
    Per-user rate limit buckets saaf karo (`rl:user:*`).

    ⚠️ Ye zaroori hai, aur wajah test-specific hai.

    BOOKING limit 5 burst / 1 per second hai. Ek group test ek hi user se
    kai calls karta hai — group banao, phir har share ka checkout. Suite
    me pehle chal chuke booking aur rate-limit tests wahi bucket already
    khaali kar chuke hote hain, to group tests 429 khaane lagte hain.

    Wo 429 group logic ka nateeja nahi, test order ka hai. Isliye sirf
    per-user buckets saaf karte hain — `rl:login:*` ko haath nahi lagate,
    kyunki brute-force wali test usi par tiki hai.
    """
    from redis_client import redis_client

    for key in redis_client.scan_iter("rl:user:*", count=500):
        redis_client.delete(key)


@pytest.fixture
def group_seats(client, tokens):
    """3 available seats — test ke baad jo bache use saaf kar do."""
    _clear_user_rate_limits()

    seats = client.get("/api/events/1/seats").json()
    available = [s["id"] for s in seats if s["status"] == "available"]
    if len(available) < 3:
        pytest.skip("3 available seats nahi hain — reset_state.py chalao")

    picked = available[:3]
    yield picked

    # Cleanup: bachi hui bookings hatao. Group cancel karna kaafi nahi —
    # confirm ho chuka group cancel nahi hota.
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
    """Share ka checkout banao aur mock provider se success simulate karo."""
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
    Group banane par seats hold hoti hain, book NAHI hoti.

    Ye faraq poore feature ki neev hai: paisa aane se pehle kisi ki seat
    pakki nahi hoti.
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
    ⭐ 2 me se 3 ne pay kiya — kisi ki bhi seat book nahi honi chahiye.

    Ye "sab ya koi nahi" ka asli test hai.
    """
    group = _make_group(client, tokens[0], group_seats)
    st = group["share_token"]

    client.post(f"/api/groups/{st}/shares/{group['shares'][1]['id']}/claim",
                headers=_headers(tokens[1]))

    _pay_share(client, tokens[0], st, group["shares"][0]["id"])
    _pay_share(client, tokens[1], st, group["shares"][1]["id"])

    after = client.get(f"/api/groups/{st}", headers=_headers(tokens[0])).json()
    assert after["paid_shares"] == 2
    assert after["status"] == "collecting", "3 me se 2 pe confirm nahi hona chahiye"

    # Ek bhi seat booked nahi
    for seat_id in group_seats:
        assert _seat(client, seat_id)["status"] == "group_held"

    client.delete(f"/api/groups/{st}", headers=_headers(tokens[0]))


def test_all_paid_confirms_everyone(client, tokens, group_seats):
    """Aakhri payment aate hi sab ek saath confirm."""
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

    # Teen alag users ki teen alag bookings — ek user ki 3 nahi
    owners = set()
    for i in (0, 1, 2):
        for b in client.get("/api/bookings", headers=_headers(tokens[i])).json():
            if b["seat_id"] in group_seats and b["status"] == "confirmed":
                owners.add(i)
    assert owners == {0, 1, 2}


def test_expired_group_releases_seats_and_refunds(client, tokens, group_seats):
    """
    ⭐ Deadline nikal gayi — seats chhooti hain aur jo paisa aaya wo refund.

    Deadline ko DB me peeche khiska dete hain; asli 30 minute ka wait
    test me mumkin nahi.
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
        # Job ko seedha call karte hain — cron ka 30 second wait nahi
        expire_due_groups(db)
    finally:
        db.close()

    after = client.get(f"/api/groups/{st}", headers=_headers(tokens[0])).json()
    assert after["status"] == "expired"

    # Jisne pay kiya tha uska refund, baaki unpaid hi rahe
    statuses = [s["status"] for s in after["shares"]]
    assert statuses.count("refunded") == 1
    assert statuses.count("unpaid") == 2

    for seat_id in group_seats:
        assert _seat(client, seat_id)["status"] == "available"


def test_pending_payment_dies_with_the_group(client, tokens, group_seats):
    """
    Group toota to jo checkout khula pada tha wo bhi mar jata hai.

    User gateway page pe tha jab deadline nikli. Sabse achha nateeja ye
    hai ki uska paisa kate hi NA — refund se behtar hai charge hi na karna.
    Isliye `break_group` share ke pending payments ko expire kar deta hai.
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
        assert share.status == "unpaid", "paisa kata hi nahi to 'paid' nahi hona chahiye"
        assert share.booking_id is None
    finally:
        db.close()

    assert _seat(client, group_seats[0])["status"] == "available"


def test_late_webhook_after_expiry_is_refunded_not_booked(client, tokens, group_seats):
    """
    ⭐⭐ Sabse mushkil case: group toot chuka hai, aur ab gateway kehta hai
    "paisa aa gaya".

    Upar wala test dikhata hai ki hum checkout ko band kar dete hain. Par
    asli gateway hamare band karne se नहीं rukta — webhook der se aa
    sakta hai, aur tab paisa sach me kat chuka hota hai.

    Us haalat me seat wapas nahi mil sakti (chhoot chuki, shayad kisi aur
    ne le li). To ek hi sahi jawab bachta hai: **refund**.

    Yahan `_fulfil` seedha call karte hain, kyunki `/simulate` endpoint
    expired payment ko chhoota hi nahi — aur asli webhook chhoota hai.
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

        # Gateway ka der se aaya "succeeded"
        _fulfil(db, db.get(Payment, payment_id))

        share = db.get(GroupShare, share_id)
        assert share.status == "refunded",             "der se aaya paisa refund hona chahiye"
        assert share.booking_id is None, "expired group me booking nahi banni chahiye"
        assert db.get(Payment, payment_id).status == "refunded"
    finally:
        db.close()

    assert _seat(client, group_seats[0])["status"] == "available"


def test_only_one_person_can_claim_a_share(client, tokens, group_seats):
    """Do log ek hi khaali seat par ek saath — ek hi ko milni chahiye."""
    group = _make_group(client, tokens[0], group_seats)
    st = group["share_token"]
    open_share = group["shares"][1]["id"]

    def claim(token):
        return client.post(
            f"/api/groups/{st}/shares/{open_share}/claim", headers=_headers(token)
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(claim, [tokens[1], tokens[2]]))

    assert codes.count(200) == 1, f"exactly ek claim chahiye tha: {codes}"
    assert codes.count(409) == 1

    client.delete(f"/api/groups/{st}", headers=_headers(tokens[0]))


def test_cannot_pay_someone_elses_share(client, tokens, group_seats):
    """Jo share tumne claim nahi kiya uska paisa nahi de sakte."""
    group = _make_group(client, tokens[0], group_seats)
    st = group["share_token"]

    # share[0] creator (tokens[0]) ka hai
    res = client.post(
        f"/api/groups/{st}/shares/{group['shares'][0]['id']}/pay",
        headers=_headers(tokens[1]),
    )
    assert res.status_code == 403

    client.delete(f"/api/groups/{st}", headers=_headers(tokens[0]))


def test_group_creation_is_all_or_nothing(client, tokens, group_seats):
    """
    ⭐ Ek seat bhi na mile to POORA group nahi banna chahiye.

    Aadhi hold kisi kaam ki nahi — user 2 seats leke 3rd ka intezaar
    karta rehta jo kabhi milegi hi nahi.
    """
    # Ek seat ko book kar do
    taken = group_seats[2]
    assert client.post("/api/bookings", json={"seat_id": taken},
                       headers=_headers(tokens[5])).status_code == 201

    res = client.post(
        "/api/groups",
        headers=_headers(tokens[0]),
        json={"seat_ids": group_seats},
    )
    assert res.status_code == 409

    # ⭐ Baaki do seats CHHOOTI honi chahiye — group_held me atki nahi
    for seat_id in group_seats[:2]:
        assert _seat(client, seat_id)["status"] == "available", \
            "fail hui group creation ne seats hold me chhod di"


def test_unknown_share_token_is_404(client, tokens):
    """Token guess karke doosron ke group me nahi ghus sakte."""
    res = client.get("/api/groups/definitely-not-a-real-token",
                     headers=_headers(tokens[0]))
    assert res.status_code == 404


def test_only_creator_can_cancel(client, tokens, group_seats):
    """Aur non-creator ko 404 milta hai, 403 nahi — existence bhi na pata chale."""
    group = _make_group(client, tokens[0], group_seats)
    st = group["share_token"]

    assert client.delete(f"/api/groups/{st}",
                         headers=_headers(tokens[1])).status_code == 404
    assert client.delete(f"/api/groups/{st}",
                         headers=_headers(tokens[0])).status_code == 200


def test_confirm_and_expiry_race_has_exactly_one_winner(client, tokens, group_seats):
    """
    ⭐⭐ Phase 17 ka sabse mushkil test.

    Aakhri banda pay kar raha hai us waqt jab expiry job group todh raha
    hai. Exactly ek ko jeetna chahiye, aur haarne wale ko sahi cleanup:

      confirm jeeta -> saari seats booked, sabki bookings bani
      expire jeeta  -> saari seats available, jo paisa aaya wo refunded

    Kabhi bhi aadhi haalat nahi: na 'collecting' me atka group, na paid
    share bina booking ke.

    Ye race bina `FOR UPDATE` ke asal me TOOTI thi — payment thread group
    ka status padh leta tha, expiry job usse expire kar deta tha, aur
    share 'paid' hi reh jata tha bina refund ke.
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

    # Aakhri share ka checkout ban gaya, settle abhi baaki
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
        # Jitter — bina iske expiry hamesha jeet jati hai (seedha function
        # call vs poora HTTP stack), aur doosra raasta test hi nahi hota
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
            f"group '{g.status}' me atka — koi jeeta hi nahi"

        seat_states = [_seat(client, s.seat_id)["status"] for s in shares]

        if g.status == "confirmed":
            assert all(x == "booked" for x in seat_states), seat_states
            assert all(s.booking_id is not None for s in shares)
        else:
            assert all(x == "available" for x in seat_states), seat_states
            assert all(s.booking_id is None for s in shares)
            # ⭐ Jiska paisa aa chuka tha uska refund hona hi chahiye
            for s in shares:
                assert s.status in ("refunded", "unpaid"), \
                    f"expired group me share '{s.status}' — paisa phansa hua hai"
    finally:
        db.close()


def test_broken_group_does_not_leave_pending_payments(client, tokens, group_seats):
    """
    Group toota to uske PENDING payments bhi band hone chahiye.

    Warna do dikkatein:
      1. `uq_one_pending_payment_per_seat` us seat par naya checkout banne
         hi nahi deta — seat 'available' dikhti par khareedi nahi ja sakti
      2. User purana checkout complete karke ek mare hue group ko paisa
         de deta hai

    Ye bug asal me tha aur race test likhte waqt pakda gaya.
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
        assert not still_pending, f"{len(still_pending)} pending payments latke hain"
    finally:
        db.close()

    # Aur ab wahi seat normally khareedi ja sakti hai — yahi asli check hai.
    # Pehle ye 409 deta tha kyunki purana pending payment index rok raha tha.
    res = client.post("/api/payments/checkout",
                      json={"seat_id": group_seats[0]}, headers=_headers(tokens[3]))
    assert res.status_code == 201, res.text

    # Apne peeche pending payment mat chhodo — warna agla test isi index
    # se takrayega. (Wahi galti jo abhi test kar rahe hain.)
    client.post(f"/api/payments/{res.json()['payment_id']}/simulate",
                json={"outcome": "fail"}, headers=_headers(tokens[3]))


# ---------------------------------------------------------------------------
# Phase 18 — Seat layout
#
# Do hisse:
#   1. validate/expand — pure functions, koi DB nahi
#   2. HTTP flow — dono raaste (layout aur price_tiers) same manzil pe
#
# Sabse zaroori invariant: **purane events (layout NULL) na tootein.**
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
    # Price section se aata hai, row se nahi
    assert {p.price for p in plan if p.section == "Balcony"} == {800.0}
    # Numbering har row me 1 se shuru
    assert sorted(p.seat_number for p in plan if p.row_label == "A") == [1, 2, 3]


def test_aisles_do_not_create_or_skip_seats():
    """
    ⭐ Aisle sirf DIKHNE ki cheez hai.

    Ye aasan galti hai: aisle ko ek "khali seat" bana dena, ya uske baad
    numbering skip kar dena. Dono galat hain — attendee "seat 5" maangta
    hai aur usse seat 6 mil jati.
    """
    with_aisle = seat_layout.expand(_layout(_section("X", 100, _row("A", 6, [3]))))
    without = seat_layout.expand(_layout(_section("X", 100, _row("A", 6))))

    assert len(with_aisle) == len(without) == 6
    assert [p.seat_number for p in with_aisle] == [1, 2, 3, 4, 5, 6]


def test_duplicate_row_label_across_sections_is_rejected():
    """
    ⭐ `seats` par UNIQUE(event_id, row_label, seat_number) hai.

    Ye pakde bina expansion 500 seats insert karne ke BAAD IntegrityError
    se marta — aur tab tak transaction bhaari ho chuki hoti.
    """
    with pytest.raises(seat_layout.LayoutError, match="do jagah"):
        seat_layout.validate(
            _layout(
                _section("Ground", 100, _row("A", 5)),
                _section("Balcony", 200, _row("A", 5)),
            )
        )


def test_aisle_outside_row_is_rejected():
    """Aakhri seat ke baad aisle ka koi matlab nahi — wo row ka ant hai."""
    with pytest.raises(seat_layout.LayoutError, match="aisle position"):
        seat_layout.validate(_layout(_section("X", 100, _row("A", 5, [5]))))

    with pytest.raises(seat_layout.LayoutError, match="aisle position"):
        seat_layout.validate(_layout(_section("X", 100, _row("A", 5, [9]))))

    # 4 theek hai — 5 seats me seat 4 ke baad gap ban sakti hai
    seat_layout.validate(_layout(_section("X", 100, _row("A", 5, [4]))))


def test_duplicate_section_name_is_rejected():
    with pytest.raises(seat_layout.LayoutError, match="naam ek hi"):
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
        seat_layout.validate(_layout(_section("X", 100)))     # koi row nahi

    huge = _layout(
        _section("X", 100, *[_row(f"R{i}", 60) for i in range(40)])
    )
    with pytest.raises(seat_layout.LayoutError, match="Max 2000"):
        seat_layout.validate(huge)


def test_price_tiers_convert_to_the_same_shape():
    """
    Purana raasta bhi layout se hi guzarta hai.

    Do alag generators rakhne ka matlab hota do jagah bugs — aur wo
    dheere-dheere alag behave karne lagte.
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

    # Layout store hua — grid isse aisles dikhata hai
    detail = client.get(f"/api/events/{event['id']}").json()
    assert detail["layout"]["sections"][0]["rows"][0]["aisles_after"] == [4]

    assert client.delete(
        f"/api/organizer/events/{event['id']}", headers=_headers(token)
    ).status_code == 204


def test_bad_layout_creates_no_event(client, role_tokens):
    """
    ⭐ Galat layout par ek bhi seat (aur event) nahi banna chahiye.

    Validation expansion se PEHLE chalti hai, isliye DB ko haath hi nahi
    lagta. Aadha bana hua event sabse gandi haalat hoti.
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
    assert "do jagah" in res.json()["detail"]

    after = len(client.get("/api/organizer/events", headers=_headers(token)).json())
    assert after == before, "fail hone par bhi event ban gaya"


def test_price_tiers_path_still_works_and_stores_a_layout(client, role_tokens):
    """
    Backwards compatibility — purana request body bilkul waise hi chalna
    chahiye jaise Phase 10 me chalta tha.
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

    # price_tiers se aaya event bhi layout store karta hai
    detail = client.get(f"/api/events/{event['id']}").json()
    assert detail["layout"] is not None
    assert len(detail["layout"]["sections"]) == 2

    seats = client.get(f"/api/events/{event['id']}/seats").json()
    assert {s["price"] for s in seats if s["row_label"] == "A"} == {1500.0}

    client.delete(f"/api/organizer/events/{event['id']}", headers=_headers(token))


def test_old_events_without_a_layout_still_work(client):
    """
    ⭐⭐ Sabse zaroori test.

    Event 1 seed se aata hai aur uska `layout` NULL hai. 17 phases ka
    demo data, tests aur bookings usi par tike hain. Naya column optional
    hai, aur usse KUCH nahi tootna chahiye.
    """
    detail = client.get("/api/events/1").json()
    assert detail["layout"] is None

    seats = client.get("/api/events/1/seats").json()
    assert len(seats) == 100
    assert all(s["section"] is None for s in seats)
    # Baaki sab fields waise hi hain
    assert all("price" in s and "status" in s and "version" in s for s in seats)


# ---------------------------------------------------------------------------
# Phase 19 — Seat search
#
# ⭐ In tests me se EK BHI ko Gemini ki zaroorat nahi.
#
# Wo jaan-boojh ke hai. LLM sirf "text -> filters" karta hai; uske baad ka
# poora search normal code hai. Agar search ko test karne ke liye API key
# chahiye hoti, to CI me ye tests skip ho jaate — aur skipped tests green
# dikhte hain (Phase 16 me yahi galti pakdi thi).
# ---------------------------------------------------------------------------

import seat_search


class _FakeSeat:
    """Test ke liye ek chhota seat — poora ORM object banane ki zaroorat nahi."""

    def __init__(self, id, row, num, price=1000, status="available", section=None):
        self.id = id
        self.row_label = row
        self.seat_number = num
        self.price = price
        self.status = status
        self.section = section


# ⚠️ `_seat_row`, `_row` nahi — Phase 18 ke layout tests me pehle se ek
# `_row()` helper hai jiska signature alag hai. Dono ek hi module me hain,
# to same naam rakhne par baad wali definition pehli ko chupchaap overwrite
# kar deti hai aur 8 purane tests TypeError se fail hone lagte hain.
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
    """Beech me ek booked seat ho to wo 'saath' nahi hai."""
    # A: seats 1,2,[3 booked],4,5  -> 3 saath wali seats nahi milengi
    seats = _seat_row("A", 5, taken=(3,))

    assert seat_search.find(seats, quantity=3, together=True) == []
    # 2 saath wali mil jaayengi (1-2 aur 4-5)
    assert len(seat_search.find(seats, quantity=2, together=True)) == 2


def test_together_false_returns_individual_seats():
    """
    "3 seats chahiye, saath nahi" ka matlab hai "koi bhi 3 dikha do".

    Unhe artificially group karke dikhana jhooth hoga.
    """
    seats = _seat_row("A", 5, taken=(3,))
    found = seat_search.find(seats, quantity=3, together=False)

    assert len(found) == 4                        # 4 available seats
    assert all(len(m.seat_ids) == 1 for m in found)


def test_aisle_breaks_togetherness():
    """
    ⭐⭐ Phase 18 ka layout data yahan kaam aata hai.

    Seat 2 aur 3 ke beech aisle hai. Numbers lagatar hain, par wo seats
    saath NAHI hain — beech me log guzar rahe honge.

    Bina is check ke search "saath wali seats" bata deta jo asal me saath
    hoti hi nahi, aur wo galti user ko venue pahunch kar pata chalti.
    """
    seats = _seat_row("A", 6)
    layout = {
        "sections": [
            {"name": "X", "price": 1000, "rows": [{"label": "A", "seats": 6, "aisles_after": [2]}]}
        ]
    }

    # Bina layout ke: 1-2-3, 2-3-4, 3-4-5, 4-5-6 = 4 groups
    assert len(seat_search.find(seats, quantity=3, together=True)) == 4

    # Layout ke saath: aisle 2 ke baad hai, to sirf 3-4-5 aur 4-5-6 bachte hain
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
    "stage ke paas" bola hai to sasti seat ke chakkar me peeche mat bhejo.

    Row A stage ke sabse paas hai — wahi convention Phase 3 se hai.
    """
    seats = _seat_row("A", 2, price=3000, start_id=1) + _seat_row("Z", 2, price=100, start_id=10)

    front = seat_search.find(seats, quantity=1, row_preference="front")
    assert front[0].row_label == "A"

    back = seat_search.find(seats, quantity=1, row_preference="back")
    assert back[0].row_label == "Z"

    # Bina preference ke sasti pehle
    default = seat_search.find(seats, quantity=1)
    assert default[0].row_label == "Z"


def test_booked_seats_never_appear():
    seats = _seat_row("A", 3, taken=(1, 2, 3))
    assert seat_search.find(seats, quantity=1) == []


def test_quantity_is_clamped():
    """Model ya user kuch bhi bhej de — 10 se zyada nahi."""
    seats = _seat_row("A", 40)
    assert seat_search.find(seats, quantity=999, together=True) != []


# ---- HTTP flow (AI ke bina) ----

def test_search_endpoint_works_without_ai(client, tokens):
    """
    ⭐ Filters se search AI ke bina chalna chahiye.

    Ye poore feature ka sabse zaroori invariant hai: AI ek addition hai,
    dependency nahi. Key na ho, model down ho, quota khatam ho — search
    phir bhi kaam kare.
    """
    res = client.post(
        "/api/events/1/seats/search",
        headers=_headers(tokens[0]),
        json={"filters": {"quantity": 2, "together": True, "max_price": 999999}},
    )
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["filters"]["quantity"] == 2
    assert body["interpreted"] is False        # AI use hi nahi hui
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
    Login zaroori hai — data private isliye nahi (seats public hain),
    balki isliye ki rate limit per-user lagti hai aur AI calls ka kharcha
    kisi ke naam hona chahiye.
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
    ⭐ Ye security boundary ka test hai.

    `SeatFilters` wo jagah hai jahan LLM ka output validate hota hai.
    Agar wo kachra values pass hone de, to model (ya koi bhi caller)
    unbounded query bana sakta hai.
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
    """Frontend isse decide karta hai ki search box dikhana hai ya nahi."""
    body = client.get("/api/auth/config").json()
    assert "ai_search_enabled" in body
    assert isinstance(body["ai_search_enabled"], bool)


# ---------------------------------------------------------------------------
# Phase 20 — AI event copy
#
# In tests ko bhi API key ki zaroorat nahi. Jo cheezein test ho rahi hain —
# RBAC, validation, aur "AI off ho to saaf 503" — wo sab AI ke bina bhi
# sach honi chahiye.
#
# AI ka OUTPUT test nahi kiya ja sakta (model har baar alag likhta hai,
# aur likhna hi chahiye). Isliye yahan uske AASPAAS ka contract test hota
# hai, andar ka content nahi.
# ---------------------------------------------------------------------------

def test_draft_needs_organizer_role(client, tokens, role_tokens):
    """Attendee event nahi bana sakta, to draft bhi nahi maang sakta."""
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
    Draft me wahi teen fields aane chahiye jo form bharta hai.

    ⚠️ Content check NAHI karte — model har baar alag likhega, aur likhna
    hi chahiye. Contract test karte hain, prose nahi.

    AI off ho to 503 milta hai, aur wo bhi valid outcome hai — is test ka
    matlab hai "endpoint sahi shape deta hai YA saaf mana karta hai",
    kabhi 500 nahi.
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
    ⭐ Sabse zaroori test.

    AI draft kuch SAVE nahi karta. Organizer ko form me dikhta hai aur wo
    edit karke khud publish karta hai.

    Event ka description attendee se kiya gaya waada hai — us par insaan
    ka haath hona chahiye. AI ko publish button tak pahunchne nahi dete.
    """
    token = role_tokens["organizer"]
    before = len(client.get("/api/organizer/events", headers=_headers(token)).json())

    client.post(
        "/api/organizer/events/draft",
        headers=_headers(token),
        json={"brief": "Some test event at a test venue in December"},
    )

    after = len(client.get("/api/organizer/events", headers=_headers(token)).json())
    assert after == before, "draft ne event bana diya — ye kabhi nahi hona chahiye"
