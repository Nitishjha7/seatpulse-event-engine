"""
Payment reconciliation.

⭐ Relying solely on webhooks is insufficient.

Webhooks can be missed due to server downtime, network issues, or exhausted
gateway retries. In such cases, the user is charged, but the booking is not
created, and the seat remains blocked.

Every payment has a TTL. This script processes pending payments that have
exceeded their TTL:

  - Query Stripe for the actual payment status.
  - If succeeded, fulfill the booking (recovering from a missed webhook).
  - Otherwise, mark as expired and release the seat.

Execution (via cron/scheduler, every 5 minutes):
    docker compose exec backend python reconcile_payments.py

This is a "belt and braces" approach; webhooks provide the fast path,
while this script acts as a safety net.
"""

import logging

import httpx
from sqlalchemy import select

from config import settings
from database import SessionLocal
from models import PAYMENT_PENDING, Payment, utcnow

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("reconcile")


def _stripe_session_status(session_id: str) -> str | None:
    """Query Stripe for the session status. Returns None if status is unknown."""
    try:
        res = httpx.get(
            f"https://api.stripe.com/v1/checkout/sessions/{session_id}",
            auth=(settings.STRIPE_SECRET_KEY, ""),
            timeout=15,
        )
        res.raise_for_status()
        return res.json().get("payment_status")   # "paid" | "unpaid" | "no_payment_required"
    except httpx.HTTPError as exc:
        logger.warning("Failed to retrieve status for %s from Stripe: %s", session_id, exc)
        return None


def reconcile() -> None:
    # Import deferred to avoid circular imports (routers.payments -> models -> ...).
    # Reconciliation must use the same _fulfil/_fail logic as webhooks to
    # ensure consistency.
    from routers.payments import _fail, _fulfil

    db = SessionLocal()
    settled = expired = 0

    try:
        stale = db.scalars(
            select(Payment).where(
                Payment.status == PAYMENT_PENDING,
                Payment.expires_at < utcnow(),
            )
        ).all()

        if not stale:
            logger.info("No stale payments found — all settled.")
            return

        logger.info("Found %d stale pending payments.", len(stale))

        for payment in stale:
            # Verify status with Stripe to handle missed webhooks.
            # Do not expire if the payment was successful.
            if payment.provider == "stripe" and payment.provider_ref:
                status = _stripe_session_status(payment.provider_ref)

                if status == "paid":
                    logger.warning(
                        "Payment %s is PAID on Stripe but pending here — "
                        "webhook missed. Fulfilling.",
                        payment.id,
                    )
                    _fulfil(db, payment)
                    settled += 1
                    continue

                if status is None:
                    # Stripe unreachable; skip to avoid premature expiration.
                    continue

            _fail(db, payment, "expired_unpaid")
            expired += 1

        logger.info("✅ Fulfilled %d (missed webhooks), expired %d.", settled, expired)

    finally:
        db.close()


if __name__ == "__main__":
    reconcile()
