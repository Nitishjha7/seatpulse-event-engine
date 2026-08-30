"""
Payment providers.

Do implementations, ek hi interface:

  StripeProvider — asli gateway (test mode)
  MockProvider   — jab Stripe keys na hon

⭐ Mock kyu banaya:
Interviewer mera repo clone karega — uske paas meri Stripe keys nahi hongi.
Bina mock ke wo poora checkout flow chala hi nahi sakta, aur "payments hain"
ka claim uske liye jhoot jaisa lagta. Ab keys ho ya na ho, flow same chalta
hai — sirf paisa asli nahi katta.

Yahi pattern Google OAuth me use kiya tha: credentials na ho to feature
gracefully band ho jata hai, poora app nahi tootta.

---- Stripe SDK kyu nahi use kiya ----

`httpx` pehle se dependency hai, aur Stripe ka REST API seedha-saada hai.
SDK add karne se ek aur dependency aati aur — zyada important — webhook
signature verification ek black box ban jaata. Wo khud likhne se pata
chalta hai ki wo actually kaam kaise karta hai (aur wo interview me
poocha jata hai).
"""

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass

import httpx

from config import settings

logger = logging.getLogger(__name__)

STRIPE_API = "https://api.stripe.com/v1"

# Webhook signature kitni purani chal sakti hai.
# Iske bina koi ek purana valid webhook capture karke baar-baar replay
# kar sakta hai — signature to valid hi rahegi hamesha.
WEBHOOK_TOLERANCE_SECONDS = 300


@dataclass
class CheckoutSession:
    """Provider se milne wala session — dono providers yahi lautate hain."""

    reference: str      # gateway ka id (webhook isi se payment dhoondhta hai)
    url: str            # user ko yahan bhejo


class PaymentError(Exception):
    """Gateway se baat karne me dikkat. Route ise 502 me badalta hai."""


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------

class MockProvider:
    name = "mock"

    def create_checkout(self, *, payment_id: int, amount: float, description: str) -> CheckoutSession:
        # Reference me payment_id daal rahe hain taki mock webhook use
        # dhoondh sake — asli gateway ye id khud generate karta hai.
        reference = f"mock_sess_{payment_id}_{int(time.time())}"

        # User ko apne hi frontend ke checkout page pe bhejte hain
        url = f"{settings.FRONTEND_URL.rstrip('/')}/pay/{payment_id}"
        return CheckoutSession(reference=reference, url=url)

    def verify_webhook(self, payload: bytes, signature: str | None) -> dict:
        raise PaymentError("Mock provider ke paas webhook nahi hota")


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------

class StripeProvider:
    name = "stripe"

    def create_checkout(self, *, payment_id: int, amount: float, description: str) -> CheckoutSession:
        frontend = settings.FRONTEND_URL.rstrip("/")

        # ⚠️ Stripe amount SABSE CHHOTI unit me leta hai — INR me paise.
        # ₹800 ko 800 bhejoge to user se ₹8 katega. Ye classic bug hai.
        minor_units = int(round(amount * 100))

        data = {
            "mode": "payment",
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": settings.CURRENCY.lower(),
            "line_items[0][price_data][unit_amount]": str(minor_units),
            "line_items[0][price_data][product_data][name]": description,
            # Success URL me session id daal rahe hain sirf UI ke liye —
            # asli confirmation webhook se aati hai, is redirect se NAHI.
            "success_url": f"{frontend}/payment/return?payment_id={payment_id}",
            "cancel_url": f"{frontend}/payment/return?payment_id={payment_id}&cancelled=1",
            # Apna payment id gateway ke paas rakh dete hain — webhook me
            # wapas milta hai, to lookup aasan ho jata hai.
            "metadata[payment_id]": str(payment_id),
            "expires_at": str(int(time.time()) + max(1800, settings.PAYMENT_TTL_SECONDS)),
        }

        try:
            res = httpx.post(
                f"{STRIPE_API}/checkout/sessions",
                data=data,
                auth=(settings.STRIPE_SECRET_KEY, ""),
                timeout=15,
            )
            res.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Stripe checkout create fail: %s", exc)
            raise PaymentError("Payment gateway se baat nahi ho payi") from exc

        body = res.json()
        return CheckoutSession(reference=body["id"], url=body["url"])

    def verify_webhook(self, payload: bytes, signature: str | None) -> dict:
        """
        ⭐ Webhook signature verify karo.

        Bina iske koi bhi hamare webhook endpoint pe POST maar ke free
        ticket le sakta hai. Ye endpoint authenticated nahi ho sakta
        (Stripe ke paas hamara token nahi hai), to signature hi uska
        authentication hai.

        Stripe header aisa bhejta hai:
            Stripe-Signature: t=1712345678,v1=abc123...,v1=def456...

        Verify karne ka tarika:
            signed_payload = "{timestamp}.{raw body}"
            expected = HMAC-SHA256(webhook_secret, signed_payload)
            expected == v1 me se koi ek?
        """
        if not signature:
            raise PaymentError("Signature header missing")

        parts = dict(
            piece.split("=", 1) for piece in signature.split(",") if "=" in piece
        )
        timestamp = parts.get("t")
        if not timestamp:
            raise PaymentError("Signature me timestamp nahi hai")

        # ⚠️ Replay protection. Signature purani hone par bhi VALID rehti hai —
        # to bina is check ke koi ek success webhook capture karke usse
        # baar-baar bhej sakta hai.
        if abs(time.time() - int(timestamp)) > WEBHOOK_TOLERANCE_SECONDS:
            raise PaymentError("Webhook timestamp bahut purana hai")

        signed = f"{timestamp}.".encode() + payload
        expected = hmac.new(
            settings.STRIPE_WEBHOOK_SECRET.encode(), signed, hashlib.sha256
        ).hexdigest()

        # Header me kai v1 ho sakte hain (secret rotate karte waqt).
        provided = [v for k, v in (p.split("=", 1) for p in signature.split(",") if "=" in p) if k == "v1"]

        # ⚠️ compare_digest — normal == timing attack ke liye khula hota hai.
        # Wo pehle mismatch pe return kar deta hai, to jawab ke time se
        # attacker ek-ek character guess kar sakta hai.
        if not any(hmac.compare_digest(expected, got) for got in provided):
            raise PaymentError("Signature match nahi hui")

        import json

        return json.loads(payload)


# ---------------------------------------------------------------------------

def get_provider():
    """Keys hain to Stripe, warna mock."""
    return StripeProvider() if settings.payment_provider == "stripe" else MockProvider()
