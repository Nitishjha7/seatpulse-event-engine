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
