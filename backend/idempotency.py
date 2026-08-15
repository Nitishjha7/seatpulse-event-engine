"""
Idempotency keys.

---- Problem ----

User "Confirm Booking" pe double-click karta hai. Ya network glitch pe
browser request retry kar deta hai. Do requests ban jaati hain.

Abhi kya hota: dusri request ko 409 milta hai — kyunki seat tab tak
`booked` ho chuki hoti. To nateeja to sahi hai.

**Par wo sanyog se sahi hai, design se nahi.** Aur user ko ek confusing
error dikhta hai jabki uski booking ho chuki hai.

Aur jab payments aayenge, ye sanyog kaafi nahi hoga — "paisa kat gaya
par booking nahi hui" wala case yahi se aata hai.

---- Solution ----

Client har booking attempt ke saath ek unique `Idempotency-Key` bhejta hai.
Server pehla jawab us key ke against Redis me store kar leta hai.

  Pehli request  -> kaam karo, jawab store karo, jawab do
  Wahi key phir  -> kaam mat karo, STORED jawab wapas do

User ko dono baar wahi booking dikhti hai. Database me ek hi row.

Ye Stripe, Razorpay, aur har payment API ka standard pattern hai.
"""

import hashlib
import json
from datetime import timedelta

from fastapi import HTTPException, Request, Response, status

from redis_client import redis_client

HEADER = "Idempotency-Key"

# Jawab kitni der yaad rakhein. 24 ghante standard hai (Stripe bhi yahi
# use karta hai) — retry aur double-click isse kahin pehle ho jaate hain.
RESULT_TTL = timedelta(hours=24)

# "Processing" wali entry ki TTL. Server beech me crash ho jaye to key
# hamesha ke liye atki na rahe — itni der me apne aap chhut jayegi.
LOCK_TTL = timedelta(seconds=60)


def _key(user_id: int, scope: str, idem_key: str) -> str:
    # user_id key me isliye: do users galti se same UUID bhej dein to
    # ek ko dusre ki booking na dikh jaye
    return f"idem:{user_id}:{scope}:{idem_key}"


def _fingerprint(payload: dict) -> str:
    """
    Request body ka hash.

    Kyu: agar koi wahi key ke saath ALAG body bhej de, to wo bug hai
    (ya attack). Chupchap purana jawab lauta dena galat hoga — isliye
    body ka hash bhi store karke compare karte hain.

    sort_keys=True — {"a":1,"b":2} aur {"b":2,"a":1} ka hash same aana chahiye.
    """
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class Idempotency:
    """
    Ek request ke liye idempotency handle.

    Use:
        idem = Idempotency(request, user.id, "booking", payload.model_dump())
        cached = idem.begin()
        if cached:
            return idem.replay(response, cached)
        ...kaam karo...
        idem.complete(result, status_code=201)
    """

    def __init__(self, request: Request, user_id: int, scope: str, payload: dict):
        self.raw_key = (request.headers.get(HEADER) or "").strip()
        self.enabled = bool(self.raw_key)
        self.fingerprint = _fingerprint(payload)
        self.redis_key = _key(user_id, scope, self.raw_key) if self.enabled else None

    def begin(self) -> dict | None:
        """
        Slot claim karo.

        None      -> naya request hai, aage badho
        dict      -> pehle ho chuka hai, ye stored jawab wapas kar do

        Raise karta hai agar wahi key alag body ke saath aaye, ya wahi
        request abhi chal rahi ho.
        """
        if not self.enabled:
            return None    # header nahi bheja — normal behaviour

        # SET NX — atomic claim. Do parallel requests me se ek hi jeetega.
        placeholder = json.dumps({"state": "processing", "fp": self.fingerprint})
        if redis_client.set(self.redis_key, placeholder, nx=True, ex=int(LOCK_TTL.total_seconds())):
            return None    # humne claim kar liya

        # Kisi aur ne (ya hum hi ne pehle) claim ki hui hai
        existing = redis_client.get(self.redis_key)
        if existing is None:
            # TTL pe abhi abhi expire ho gayi — naya maan lo
            return None

        record = json.loads(existing)

        if record.get("fp") != self.fingerprint:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Ye Idempotency-Key pehle alag data ke saath use ho chuki hai",
            )

        if record.get("state") == "processing":
            # Pehli request abhi chal rahi hai (double-click ka asli case).
            # 409 dete hain — client thodi der baad retry kar sakta hai.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Yahi request abhi process ho rahi hai",
            )

        return record

    def replay(self, response: Response, record: dict) -> dict:
        """Stored jawab wapas do."""
        response.status_code = record.get("status", 200)
        # Client ko pata chale ki ye naya kaam nahi, purana jawab hai
        response.headers["X-Idempotent-Replay"] = "true"
        return record["body"]

    def complete(self, body: dict, status_code: int = 200) -> None:
        """Kaam ho gaya — jawab store kar do."""
        if not self.enabled:
            return

        redis_client.setex(
            self.redis_key,
            RESULT_TTL,
            json.dumps(
                {
                    "state": "done",
                    "fp": self.fingerprint,
                    "status": status_code,
                    "body": body,
                },
                default=str,   # datetime waqerah ke liye
            ),
        )

    def abort(self) -> None:
        """
        Kaam fail ho gaya — claim chhod do.

        Zaroori hai: warna 500 ke baad user usi key se retry hi nahi kar
        paata, aur 60 second tak "already processing" milta rehta.
        """
        if self.enabled:
            redis_client.delete(self.redis_key)
