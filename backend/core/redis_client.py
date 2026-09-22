"""
Redis distributed seat locking.

When a user selects a seat, hold it for 5 minutes so payment can go through
without another user grabbing it.

Why Redis instead of Postgres:
  1. Speed — in-memory lock checks run in ~0.1ms, absorbing high-concurrency
     traffic before it hits the database.
  2. TTL — Redis expires keys on its own, so abandoned carts don't need a
     manual cleanup job.

Redis is a fast, load-reducing filter here, not an ACID guarantee — Postgres
constraints are still the final source of truth.
"""

import redis

from core.config import settings

# decode_responses=True returns strings instead of bytes, so no manual .decode().
redis_client = redis.Redis.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=3,
    socket_timeout=3,
)


def _lock_key(seat_id: int) -> str:
    """seat:42:lock — namespaced Redis key."""
    return f"seat:{seat_id}:lock"


# ---------------------------------------------------------------------------
# Lua script for atomic lock release
# ---------------------------------------------------------------------------
# Why not a plain DEL: User A's lock expires after 5 min, User B acquires it
# immediately, then A's delayed "release" request arrives and DELs — wiping
# out B's valid lock. This script checks ownership before deleting, and
# running it as Lua keeps the GET+DEL atomic so nothing can race in between.
_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

_release_lock = redis_client.register_script(_RELEASE_SCRIPT)


def acquire_seat_lock(seat_id: int, user_id: int, ttl: int | None = None) -> bool:
    """
    Acquire a lock on a seat. Returns True if acquired, False if already
    held by someone else.

    Uses atomic SET NX EX (e.g. SET seat:42:lock 7 NX EX 300) — nx=True only
    succeeds if the key doesn't exist yet, so only one of many concurrent
    requests wins.
    """
    return bool(
        redis_client.set(
            _lock_key(seat_id),
            str(user_id),
            nx=True,
            ex=ttl or settings.SEAT_LOCK_TTL,
        )
    )


def release_seat_lock(seat_id: int, user_id: int) -> bool:
    """Release the lock. The Lua script ensures only the owner can release it."""
    return bool(_release_lock(keys=[_lock_key(seat_id)], args=[str(user_id)]))


def get_lock_owner(seat_id: int) -> int | None:
    """Returns the user_id of the lock owner, or None if unlocked."""
    owner = redis_client.get(_lock_key(seat_id))
    return int(owner) if owner else None


def get_lock_ttl(seat_id: int) -> int:
    """
    Returns remaining TTL in seconds. Redis returns -2 (key missing) or -1
    (no TTL); both map to 0 here to keep caller logic simple.
    """
    ttl = redis_client.ttl(_lock_key(seat_id))
    return ttl if ttl > 0 else 0


def is_lock_owner(seat_id: int, user_id: int) -> bool:
    return get_lock_owner(seat_id) == user_id


def ping() -> bool:
    """Health check."""
    try:
        return redis_client.ping()
    except redis.RedisError:
        return False
