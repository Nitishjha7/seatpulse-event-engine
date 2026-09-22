"""
Idempotency keys.

Double-clicking "Confirm Booking" or a browser retry after a network glitch
can send the same request twice. Without this, the seat is already `booked`
by the second request and it just gets a 409 — a confusing error after a
booking that actually succeeded. For payments it's worse: "money deducted
but booking failed" territory.

Fix: the client sends a unique `Idempotency-Key` per booking attempt, and the
server caches the first response in Redis under that key.

  First request      -> process, store response, return it
  Repeat with same key -> skip processing, return the stored response

Same pattern Stripe and Razorpay use.
"""

import hashlib
import json
from datetime import timedelta

from fastapi import HTTPException, Request, Response, status

from core.redis_client import redis_client

HEADER = "Idempotency-Key"

# TTL for stored results. 24 hours is standard (used by Stripe) to cover
# retries and double-clicks.
RESULT_TTL = timedelta(hours=24)

# TTL for "processing" entries. Prevents keys from being permanently locked
# if the server crashes during execution.
LOCK_TTL = timedelta(seconds=60)


def _key(user_id: int, scope: str, idem_key: str) -> str:
    # Include user_id to prevent cross-user booking collisions if UUIDs overlap.
    return f"idem:{user_id}:{scope}:{idem_key}"


def _fingerprint(payload: dict) -> str:
    """
    Hash the request body so we can detect the same key reused with a
    different payload (a bug or an attack). sort_keys=True makes
    {"a":1,"b":2} and {"b":2,"a":1} hash the same.
    """
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class Idempotency:
    """
    Handles idempotency for a single request.

    Usage:
        idem = Idempotency(request, user.id, "booking", payload.model_dump())
        cached = idem.begin()
        if cached:
            return idem.replay(response, cached)
        ...process logic...
        idem.complete(result, status_code=201)
    """

    def __init__(self, request: Request, user_id: int, scope: str, payload: dict):
        self.raw_key = (request.headers.get(HEADER) or "").strip()
        self.enabled = bool(self.raw_key)
        self.fingerprint = _fingerprint(payload)
        self.redis_key = _key(user_id, scope, self.raw_key) if self.enabled else None

    def begin(self) -> dict | None:
        """
        Claim a processing slot.

        Returns:
            None: New request, proceed.
            dict: Existing request, return stored response.

        Raises:
            HTTPException: If the key is reused with a different body or
                           if the request is currently in progress.
        """
        if not self.enabled:
            return None    # no header provided; proceed normally

        # SET NX is an atomic claim — only one of several parallel requests wins.
        placeholder = json.dumps({"state": "processing", "fp": self.fingerprint})
        if redis_client.set(self.redis_key, placeholder, nx=True, ex=int(LOCK_TTL.total_seconds())):
            return None    # Claim successful.

        # Key already claimed.
        existing = redis_client.get(self.redis_key)
        if existing is None:
            # Key expired; treat as a new request.
            return None

        record = json.loads(existing)

        if record.get("fp") != self.fingerprint:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "This Idempotency-Key has already been used with different data.",
            )

        if record.get("state") == "processing":
            # Request is currently in progress (e.g., double-click).
            # Return 409 to signal the client to retry later.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This request is currently being processed.",
            )

        return record

    def replay(self, response: Response, record: dict) -> dict:
        """Return the stored response."""
        response.status_code = record.get("status", 200)
        # Indicate to the client that this is a cached response.
        response.headers["X-Idempotent-Replay"] = "true"
        return record["body"]

    def complete(self, body: dict, status_code: int = 200) -> None:
        """Store the final result."""
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
                default=str,   # Handle datetime objects.
            ),
        )

    def abort(self) -> None:
        """
        Release the claim if the operation fails.

        Necessary to allow retries immediately after a 500 error, rather
        than waiting for the 60-second lock to expire.
        """
        if self.enabled:
            redis_client.delete(self.redis_key)
