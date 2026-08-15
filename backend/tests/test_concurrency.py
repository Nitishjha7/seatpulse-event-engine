"""
Concurrency tests — chalti hui API ke against.

Ye unit tests nahi hain. Ye asli HTTP requests bhejte hain, kyunki race
conditions sirf tab dikhti hain jab poora stack (uvicorn + Redis + Postgres)
saath me chal raha ho. Mock kar dete to woh bug pakde hi nahi jaate jo
load test ne pakda tha.

Chalao:
    docker compose exec backend pytest tests/ -v
"""

import os
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

# Container ke andar se "backend", host se "localhost"
BASE_URL = os.getenv("TEST_BASE_URL", "http://backend:8000")

CONCURRENCY = 40


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c:
        yield c


@pytest.fixture
def free_seat(client):
    """
    Ek available seat dhoondho aur test ke baad usse saaf kar do.

    Har test apni seat pe kaam kare — warna tests ek dusre ko todenge.
    """
    seats = client.get("/api/events/1/seats").json()
    available = [s for s in seats if s["status"] == "available"]
    if not available:
        pytest.skip("Koi available seat nahi — reset script chalao")

    seat = available[-1]        # aakhri wali, taki UI wali se na takraye
    yield seat

    # Cleanup: lock chhodo, booking cancel karo
    for user_id in range(1, CONCURRENCY + 1):
        client.delete(f"/api/seats/{seat['id']}/lock", params={"user_id": user_id})

    for booking in client.get("/api/bookings", params={"user_id": 1}).json():
        if booking["seat_id"] == seat["id"] and booking["status"] == "confirmed":
            client.delete(f"/api/bookings/{booking['id']}")


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "healthy"
    assert body["database"] == "connected"
    assert body["redis"] == "connected"


def test_only_one_user_gets_the_lock(client, free_seat):
    """40 users, ek seat — sirf ek ko lock milna chahiye."""
    seat_id = free_seat["id"]

    def try_lock(user_id):
        return client.post(
            f"/api/seats/{seat_id}/lock", json={"user_id": user_id}
        ).status_code

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        codes = list(pool.map(try_lock, range(1, CONCURRENCY + 1)))

    assert codes.count(200) == 1, f"Expected exactly 1 lock, got {codes.count(200)}"
    assert codes.count(409) == CONCURRENCY - 1


def test_no_double_booking(client, free_seat):
    """40 users ek saath book karein — database me exactly 1 booking."""
    seat_id = free_seat["id"]

    def try_book(user_id):
        return client.post(
            "/api/bookings", json={"seat_id": seat_id, "user_id": user_id}
        ).status_code

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        codes = list(pool.map(try_book, range(1, CONCURRENCY + 1)))

    assert codes.count(201) == 1, f"Expected exactly 1 booking, got {codes.count(201)}"

    # API bhi wahi kahe
    seat = client.get(f"/api/seats/{seat_id}").json()
    assert seat["status"] == "booked"


def test_lock_blocks_other_users_booking(client, free_seat):
    """Ek user hold kare, dusra book na kar paaye."""
    seat_id = free_seat["id"]

    assert client.post(f"/api/seats/{seat_id}/lock", json={"user_id": 2}).status_code == 200

    res = client.post("/api/bookings", json={"seat_id": seat_id, "user_id": 3})
    assert res.status_code == 409

    # Lock wala khud book kar sakta hai
    assert client.post("/api/bookings", json={"seat_id": seat_id, "user_id": 2}).status_code == 201


def test_cannot_release_someone_elses_lock(client, free_seat):
    """Lua script dusre ka lock nahi hatne deti."""
    seat_id = free_seat["id"]

    client.post(f"/api/seats/{seat_id}/lock", json={"user_id": 2})

    res = client.delete(f"/api/seats/{seat_id}/lock", params={"user_id": 99})
    assert res.json()["released"] is False

    # Lock abhi bhi user 2 ke paas
    assert client.get(f"/api/seats/{seat_id}/lock").json()["locked_by"] == 2


def test_version_increments_on_change(client, free_seat):
    """Har state change pe version badhna chahiye — optimistic locking isi par chalti hai."""
    seat_id = free_seat["id"]
    before = client.get(f"/api/seats/{seat_id}").json()["version"]

    client.post(f"/api/seats/{seat_id}/lock", json={"user_id": 2})
    after_lock = client.get(f"/api/seats/{seat_id}").json()["version"]
    assert after_lock > before

    client.post("/api/bookings", json={"seat_id": seat_id, "user_id": 2})
    after_book = client.get(f"/api/seats/{seat_id}").json()["version"]
    assert after_book > after_lock
