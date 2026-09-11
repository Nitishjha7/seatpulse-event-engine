"""
Rate limiting — Redis token bucket.

The project's whole premise is "flash sales attract bots", but until this
point nothing actually stopped them.

---- Why a token bucket ----

  Fixed window (60 requests per minute):
      Simple, but allows a 2x burst at the boundary — 60 requests in
      second 59 and 60 more in second 61 is 120 in one second.

  Sliding window log (store every request timestamp):
      Exact, but stores a timestamp per request. Memory-hungry.

  Token bucket (chosen):
      A bucket holds `capacity` tokens and refills at `refill` tokens per
      second. Each request costs one token.

      This permits natural user behaviour — clicking through 4-5 seats
      quickly is fine — while a script running at 100 req/s is throttled
      down to the refill rate.

---- What the limit is keyed on ----

  Per USER (or email), never per IP.

  In production the app sits behind a load balancer or proxy, so every
  request appears to come from one IP unless X-Forwarded-For is configured
  correctly — and that header can be spoofed. Behind NAT, an entire office
  shares one IP, so blocking it for one bot punishes everyone.

  Per-IP limiting belongs at the edge (nginx, Cloudflare). The application
  limits on identity, which is far more targeted.
"""

import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Response, status

from auth import get_current_user
from config import settings
from models import User
from redis_client import redis_client

# ---------------------------------------------------------------------------
# Token bucket in Lua, because the update must be atomic
# ---------------------------------------------------------------------------
# In Python this would be GET tokens -> calculate -> SET tokens. Between
# those three steps a second request reads the same stale token count and
# both are allowed — a classic read-modify-write race.
#
# A Lua script runs inside Redis as a single unit; nothing interleaves.
_BUCKET_SCRIPT = """
local key      = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill   = tonumber(ARGV[2])   -- tokens per second
local now      = tonumber(ARGV[3])
local cost     = tonumber(ARGV[4])

local bucket = redis.call("HMGET", key, "tokens", "ts")
local tokens = tonumber(bucket[1])
local ts     = tonumber(bucket[2])

-- First request for this key: start with a full bucket
if tokens == nil then
    tokens = capacity
    ts = now
end

-- Refill for the time elapsed since the last call, capped at capacity
local elapsed = math.max(0, now - ts)
tokens = math.min(capacity, tokens + elapsed * refill)

-- cost = 0 means "peek" — only asking whether the bucket is empty.
-- Even then at least 1 token must be available, otherwise `0 >= 0` is
-- always true and an empty bucket would be allowed. (Caught by a test.)
local needed = cost
if cost == 0 then
    needed = 1
end

local allowed = 0
if tokens >= needed then
    tokens = tokens - cost      -- a peek has cost 0, so nothing is spent
    allowed = 1
end

redis.call("HMSET", key, "tokens", tokens, "ts", now)
-- Expire after the time it takes to refill completely. Past that point the
-- key carries no information (it would be a full bucket anyway), so Redis
-- cleans up idle keys on its own.
redis.call("EXPIRE", key, math.ceil(capacity / refill) + 60)

-- How long until the next token becomes available
local retry_after = 0
if allowed == 0 then
    retry_after = math.ceil((needed - tokens) / refill)
end

return {allowed, math.floor(tokens), retry_after}
"""

_bucket = redis_client.register_script(_BUCKET_SCRIPT)


@dataclass(frozen=True)
class Limit:
    """capacity = burst allowance; refill = tokens returned per second."""

    capacity: int
    refill: float

    @property
    def label(self) -> str:
        return f"{self.capacity} burst, {self.refill}/s"


# Per-endpoint budgets. The reasoning behind each number:
#
#   SEAT_LOCK  — a user may try 4-5 seats quickly (burst 15), but a
#                sustained rate above 5/s means a script
#   BOOKING    — booking is a deliberate action, never rapid-fire
#   LOGIN_FAIL — consumed only on a WRONG password. Five mistakes are
#                allowed, then one attempt per minute. Credential stuffing
#                dies here
#   REGISTER   — stops one client farming accounts
SEAT_LOCK = Limit(capacity=15, refill=5)
BOOKING = Limit(capacity=5, refill=1)
LOGIN_FAIL = Limit(capacity=5, refill=1 / 60)
REGISTER = Limit(capacity=5, refill=1 / 120)


def check(bucket_key: str, limit: Limit, cost: int = 1) -> tuple[bool, int, int]:
    """
    Try to spend a token.

    Returns: (allowed, tokens_remaining, retry_after_seconds)

    ⚠️ If Redis is down the request is ALLOWED (fail-open). Failing closed
    would take the whole site down with Redis. Rate limiting is a
    protection, not a correctness guarantee — and booking correctness
    already has three independent layers.
    """
    if not settings.RATE_LIMIT_ENABLED:
        return True, limit.capacity, 0

    try:
        allowed, remaining, retry_after = _bucket(
            keys=[f"rl:{bucket_key}"],
            args=[limit.capacity, limit.refill, time.time(), cost],
        )
        return bool(allowed), int(remaining), int(retry_after)
    except Exception:
        return True, limit.capacity, 0


def enforce(response: Response, bucket_key: str, limit: Limit) -> None:
    """Check the limit and set headers. Raises 429 when exceeded."""
    allowed, remaining, retry_after = check(bucket_key, limit)

    # Sent on every response, not just on 429, so a client can see how
    # close it is to the limit and slow itself down.
    response.headers["X-RateLimit-Limit"] = str(limit.capacity)
    response.headers["X-RateLimit-Remaining"] = str(remaining)

    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests — please slow down and try again",
            # Retry-After is a standard header; well-behaved clients read
            # it and wait accordingly.
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit.capacity),
                "X-RateLimit-Remaining": "0",
            },
        )


def limit_user(limit: Limit):
    """
    Dependency that rate-limits a logged-in user.

    Usage:
        @router.post("/x", dependencies=[Depends(limit_user(SEAT_LOCK))])
    """

    def dependency(
        response: Response,
        user: User = Depends(get_current_user),
    ) -> None:
        enforce(response, f"user:{user.id}", limit)

    return dependency


def client_ip(request: Request) -> str:
    """
    Best-effort client IP.

    ⚠️ X-Forwarded-For **can be spoofed** unless a trusted proxy sets it.
    It is therefore used only as a best-effort key on unauthenticated
    endpoints, never for a security decision.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
