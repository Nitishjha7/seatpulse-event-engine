"""
Shared constants and helpers for the integration suite.

Fixtures live in conftest.py; this module holds plain values and functions
that test files import directly.
"""

import os

import httpx

# "backend" from inside the container, "localhost" from the host
BASE_URL = os.getenv("TEST_BASE_URL", "http://backend:8000")

# seed.py assigns this password to all test users
PASSWORD = "demo1234"
CONCURRENCY = 40


def login(client: httpx.Client, email: str) -> str:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    res.raise_for_status()
    return res.json()["access_token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def clear_user_rate_limits() -> None:
    """
    Clear per-user rate limit buckets (`rl:user:*`).

    The BOOKING limit is 5 burst / 1 per second, and it is shared by every
    Redis key `rl:user:{id}` regardless of which endpoint touches it. The
    "attendee" account in `role_tokens` is the same account as `tokens[0]`
    (both are `demo@seatpulse.dev`), and that account books, locks, and
    checks out seats throughout the suite. A test file that runs shortly
    after one of those calls can find the bucket still drained and get a
    429 that has nothing to do with what it is actually testing.

    Any test relying on a fresh bucket for a shared account should call this
    first. We only clear per-user buckets — `rl:login:*` is left alone, since
    the brute-force login test relies on it.
    """
    from redis_client import redis_client

    for key in redis_client.scan_iter("rl:user:*", count=500):
        redis_client.delete(key)
