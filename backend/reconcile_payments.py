"""
Payment reconciliation.

⭐ Sirf webhook par bharosa karna kaafi nahi hai.

Webhook miss ho sakta hai — hamara server neeche tha, network gira, ya
gateway ne saare retries khatam kar diye. Us case me paisa kat chuka hoga
par booking nahi bani hogi, aur user ki seat block padi rahegi.

Isliye har payment ka ek TTL hai, aur ye script un pending payments ko
utha ke settle karti hai jinka time nikal gaya:

  - Stripe se poochho ki asal me kya hua
  - succeeded hai to fulfil karo (webhook late ya miss hua tha)
  - warna expired mark karke seat wapas available karo

Chalao (cron/scheduler se, har 5 minute):
    docker compose exec backend python reconcile_payments.py

Ye "belt and braces" hai — webhook fast path hai, ye safety net.
Har paise wale system me dono hote hain.
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
    """Stripe se poochho ki session ka kya hua. None = pata nahi chala."""
    try:
        res = httpx.get(
            f"https://api.stripe.com/v1/checkout/sessions/{session_id}",
            auth=(settings.STRIPE_SECRET_KEY, ""),
            timeout=15,
        )
        res.raise_for_status()
        return res.json().get("payment_status")   # "paid" | "unpaid" | "no_payment_required"
    except httpx.HTTPError as exc:
        logger.warning("Stripe se %s ka status nahi mila: %s", session_id, exc)
        return None


def reconcile() -> None:
    # Import yahan hai, upar nahi — warna circular import ho jata
    # (routers.payments -> models -> ... ). Reconciliation ko wahi
    # _fulfil/_fail use karne chahiye jo webhook use karta hai, apna
    # duplicate logic nahi.
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
            logger.info("Koi stale payment nahi — sab settled hai")
            return

        logger.info("%d stale pending payments mile", len(stale))

        for payment in stale:
            # Stripe hai to pehle usse poochho — ho sakta hai paisa kat chuka ho
            # aur sirf webhook miss hua ho. Bina poochhe expire kar dena
            # matlab user ka paisa le lena aur ticket na dena.
            if payment.provider == "stripe" and payment.provider_ref:
                status = _stripe_session_status(payment.provider_ref)

                if status == "paid":
                    logger.warning(
                        "Payment %s Stripe pe PAID hai par yahan pending — "
                        "webhook miss hua. Fulfil kar rahe hain.",
                        payment.id,
                    )
                    _fulfil(db, payment)
                    settled += 1
                    continue

                if status is None:
                    # Stripe se baat nahi hui — chhod do, agli baar dekhenge.
                    # Andaza laga ke expire karna galat hoga.
                    continue

            _fail(db, payment, "expired_unpaid")
            expired += 1

        logger.info("✅ %d fulfil kiye (missed webhooks), %d expire kiye", settled, expired)

    finally:
        db.close()


if __name__ == "__main__":
    reconcile()
