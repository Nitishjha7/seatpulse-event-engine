"""
Rate limiting — Redis token bucket.

⭐ Ye poore project ki kahani complete karta hai. Project ki premise hi ye
hai ki "flash sale me bots aate hain", par ab tak unhe rokne ka koi
intezaam nahi tha.

---- Algorithm kyu token bucket ----

  Fixed window (har minute me 60 requests):
      Simple hai, par boundary pe 2x burst nikal jata hai — 59th second me
      60 requests, aur 61st second me 60 aur. Ek second me 120.

  Sliding window log (har request ka timestamp store karo):
      Bilkul accurate, par har request ka timestamp rakhna padta hai.
      Memory bahut khaata hai.

  Token bucket (jo humne liya):
      Bucket me `capacity` tokens hote hain, aur `refill` tokens/second
      ki speed se bharte rehte hain. Har request ek token khaati hai.

      Faayda: user ka natural behaviour allow hota hai — 4-5 seats jaldi
      jaldi click karna theek hai (burst) — par ek script jo 100 req/s
      maar raha hai wo refill rate pe aake atak jata hai.

---- Limit kis cheez par ----

  Per USER (ya email), per IP nahi.

  Wajah: production me app load balancer/proxy ke peeche hoti hai, to
  usse har request ek hi IP se aati dikhti hai (jab tak X-Forwarded-For
  sahi se configure na ho — aur wo header spoof bhi ho sakta hai).
  Aur NAT ke peeche poora office ek IP share karta hai — ek bot ki wajah
  se sabko block karna galat hai.

  Per-IP limiting **edge par** honi chahiye (nginx, Cloudflare), app me
  nahi. App identity par limit lagata hai — wo zyada targeted hai.
"""

import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Response, status

from auth import get_current_user
from config import settings
from models import User
from redis_client import redis_client

# ---------------------------------------------------------------------------
# Token bucket — Lua me, kyunki atomic hona zaroori hai
# ---------------------------------------------------------------------------
# Python me karte to: GET tokens -> calculate -> SET tokens.
# Un teen steps ke beech dusra request bhi wahi purane tokens padh leta,
# aur dono ko permission mil jaati. Classic read-modify-write race.
#
# Lua script Redis ke andar ek unit me chalti hai — beech me kuch nahi ghus sakta.
_BUCKET_SCRIPT = """
local key      = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill   = tonumber(ARGV[2])   -- tokens per second
local now      = tonumber(ARGV[3])
local cost     = tonumber(ARGV[4])

local bucket = redis.call("HMGET", key, "tokens", "ts")
local tokens = tonumber(bucket[1])
local ts     = tonumber(bucket[2])

-- Pehli baar: bucket poora bhara hua milta hai
if tokens == nil then
    tokens = capacity
    ts = now
end

-- Pichhli baar se ab tak jitna time beeta, utne tokens bhar do (capacity tak)
local elapsed = math.max(0, now - ts)
tokens = math.min(capacity, tokens + elapsed * refill)

-- cost = 0 matlab "peek" — sirf poochh rahe hain ki bucket khali to nahi.
-- Us case me bhi kam se kam 1 token hona chahiye, warna 0 >= 0 hamesha
-- true hota aur khali bucket bhi allow ho jata. (Ye bug test me pakda gaya.)
local needed = cost
if cost == 0 then
    needed = 1
end

local allowed = 0
if tokens >= needed then
    tokens = tokens - cost      -- peek me cost 0 hai, to kuch ghata nahi
    allowed = 1
end

redis.call("HMSET", key, "tokens", tokens, "ts", now)
-- Bucket poora bharne me jitna time lagega, utni TTL. Uske baad key ka
-- koi matlab nahi (wo waise bhi full bucket hi hoti). Isse Redis khud
-- purani keys saaf karta rehta hai.
redis.call("EXPIRE", key, math.ceil(capacity / refill) + 60)

-- Agla token kitni der me milega
local retry_after = 0
if allowed == 0 then
    retry_after = math.ceil((needed - tokens) / refill)
end

return {allowed, math.floor(tokens), retry_after}
"""

_bucket = redis_client.register_script(_BUCKET_SCRIPT)


@dataclass(frozen=True)
class Limit:
    """capacity = ek saath kitne allowed; refill = kitne tokens/second wapas."""

    capacity: int
    refill: float

    @property
    def label(self) -> str:
        return f"{self.capacity} burst, {self.refill}/s"


# Har endpoint ka apna budget. Numbers ka logic:
#
#   SEAT_LOCK  — user 4-5 seats jaldi try kar sakta hai (burst 15), par
#                sustained 5/s se zyada matlab script hai
#   BOOKING    — booking soch ke hoti hai, itni tez nahi
#   LOGIN_FAIL — sirf GALAT password pe consume hota hai. 5 galtiyan
#                allowed, phir har minute me ek chance. Credential
#                stuffing yahin mar jaati hai
#   REGISTER   — ek IP se account farm banane se rokta hai
SEAT_LOCK = Limit(capacity=15, refill=5)
BOOKING = Limit(capacity=5, refill=1)
LOGIN_FAIL = Limit(capacity=5, refill=1 / 60)
REGISTER = Limit(capacity=5, refill=1 / 120)


def check(bucket_key: str, limit: Limit, cost: int = 1) -> tuple[bool, int, int]:
    """
    Ek token lene ki koshish.

    Returns: (allowed, tokens_bache, retry_after_seconds)

    ⚠️ Redis down ho to hum ALLOW karte hain (fail-open).
    Fail-closed karte to Redis girte hi poori site band ho jaati.
    Rate limiting ek protection hai, correctness nahi — aur booking ki
    correctness ki teen alag layers pehle se hain.
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
    """Limit check karo aur headers set karo. Limit paar ho to 429."""
    allowed, remaining, retry_after = check(bucket_key, limit)

    # Ye headers hamesha bhejte hain (sirf 429 pe nahi) — client dekh sakta
    # hai ki wo limit ke kitna paas hai aur khud slow ho sakta hai.
    response.headers["X-RateLimit-Limit"] = str(limit.capacity)
    response.headers["X-RateLimit-Remaining"] = str(remaining)

    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Bahut zyada requests — thoda ruk ke try karo",
            # Retry-After standard header hai. Achhe clients ise padh ke
            # itni der wait karte hain.
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit.capacity),
                "X-RateLimit-Remaining": "0",
            },
        )


def limit_user(limit: Limit):
    """
    Logged-in user par limit lagane wali dependency.

    Use:
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
    Client ka IP.

    ⚠️ X-Forwarded-For **spoof ho sakta hai** jab tak koi trusted proxy
    use set na kare. Isliye ise sirf unauthenticated endpoints par
    best-effort ki tarah use kar rahe hain, kisi security decision ke
    liye nahi.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
