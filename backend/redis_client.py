"""
Redis distributed seat locking.

⭐ Core component of Phase 4. Critical for interview assessments.

Purpose:
  When a user selects a seat, hold it for 5 minutes to allow payment processing
  without interference from other users.

Why Redis instead of Postgres:
  1. Speed: In-memory operations ensure lock checks take ~0.1ms. This acts as a
     buffer, preventing high-concurrency traffic (e.g., 5000 requests) from
     hitting the database.
  2. TTL: Redis handles key expiration automatically, eliminating the need for
     manual cleanup jobs for abandoned carts.

Note: Redis provides a fast, load-reducing filter, not a strict ACID guarantee.
Postgres constraints remain the final source of truth.
"""

import redis

from config import settings

# decode_responses=True returns strings instead of bytes, avoiding manual .decode().
redis_client = redis.Redis.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=3,
    socket_timeout=3,
)


def _lock_key(seat_id: int) -> str:
    """seat:42:lock — Namespacing for organized Redis keys."""
    return f"seat:{seat_id}:lock"


# ---------------------------------------------------------------------------
# Lua script for atomic lock release
# ---------------------------------------------------------------------------
# Why not a simple DEL:
#
#   1. User A's lock expires after 5 minutes.
#   2. User B acquires the lock immediately.
#   3. User A's delayed "release" request arrives and executes DEL.
#      -> User B's valid lock is incorrectly deleted.
#
# The script verifies ownership before deletion. Executing this as a Lua script
# ensures atomicity within Redis, preventing race conditions between GET and DEL.
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
    Acquire a lock on a seat.

    Returns:
        True: Lock acquired.
        False: Lock held by another user.

    Uses atomic SET with NX and EX options:
        SET seat:42:lock 7 NX EX 300

    nx=True ensures the operation only succeeds if the key does not exist.
    This is atomic, ensuring only one of many concurrent requests succeeds.
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
    """
    Release the lock. The Lua script ensures only the owner can release it.

    Returns:
        True: Lock was held by the user and successfully released.
    """
    return bool(_release_lock(keys=[_lock_key(seat_id)], args=[str(user_id)]))


def get_lock_owner(seat_id: int) -> int | None:
    """Returns the user_id of the lock owner, or None if unlocked."""
    owner = redis_client.get(_lock_key(seat_id))
    return int(owner) if owner else None


def get_lock_ttl(seat_id: int) -> int:
    """
    Returns remaining TTL in seconds.

    Redis returns -2 (key missing) or -1 (no TTL).
    Returns 0 for these cases to simplify caller logic.
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
