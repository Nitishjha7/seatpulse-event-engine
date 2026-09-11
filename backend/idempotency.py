"""
Idempotency keys.

---- Problem ----

Users may double-click "Confirm Booking" or browsers may retry requests due to
network glitches, leading to duplicate requests.

Currently, the second request receives a 409 error because the seat is already
`booked`. While the outcome is correct, it is accidental rather than by design,
resulting in a confusing error for the user despite a successful booking.

For payment processing, this approach is insufficient and can lead to
"money deducted but booking failed" scenarios.

---- Solution ----

The client sends a unique `Idempotency-Key` with each booking attempt. The
server stores the initial response in Redis keyed by this value.

  First request -> Process, store response, return response
  Subsequent request -> Do not process, return STORED response

The user sees the same booking result both times, and only one row is created
in the database.

This is the standard pattern used by Stripe, Razorpay, and other payment APIs.
"""

import hashlib
import json
from datetime import timedelta

from fastapi import HTTPException, Request, Response, status

from redis_client import redis_client

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
    Generate a hash of the request body.

    Used to detect if the same key is reused with a different body, which
    indicates a bug or attack. We store the hash to validate consistency.

    sort_keys=True ensures {"a":1,"b":2} and {"b":2,"a":1} produce the same hash.
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
            return None    # No header provided; proceed normally.

        # SET NX provides an atomic claim. Only one of multiple parallel requests succeeds.
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
