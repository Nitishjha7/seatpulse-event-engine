"""
Payment providers.

Two implementations behind one interface:

  StripeProvider — real gateway, test mode
  MockProvider   — used when Stripe keys aren't set, so checkout still
                   works end to end without needing a Stripe account.
                   Same degrade-gracefully pattern as Google OAuth.

No Stripe SDK — httpx was already a dependency and the REST API is simple
enough that pulling in another package wasn't worth it. Writing the webhook
signature check by hand also means I actually know what it's doing instead
of trusting a library method I've never read.
"""

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

STRIPE_API = "https://api.stripe.com/v1"

# Maximum age for a webhook request.
# Prevents replay attacks where an attacker captures a valid webhook
# and resends it to trigger duplicate processing.
WEBHOOK_TOLERANCE_SECONDS = 300


@dataclass
class CheckoutSession:
    """Session object returned by providers."""

    reference: str      # Gateway ID (used for webhook lookups)
    url: str            # Redirect URL for the user


class PaymentError(Exception):
    """Gateway communication error. Handled by routes as 502."""


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------

class MockProvider:
    name = "mock"

    def create_checkout(self, *, payment_id: int, amount: float, description: str) -> CheckoutSession:
        # Embed payment_id in the reference to allow the mock webhook
        # to identify the transaction.
        reference = f"mock_sess_{payment_id}_{int(time.time())}"

        # Redirect to the local frontend checkout page.
        url = f"{settings.FRONTEND_URL.rstrip('/')}/pay/{payment_id}"
        return CheckoutSession(reference=reference, url=url)

    def verify_webhook(self, payload: bytes, signature: str | None) -> dict:
        raise PaymentError("Mock provider does not support webhooks")


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------

class StripeProvider:
    name = "stripe"

    def create_checkout(self, *, payment_id: int, amount: float, description: str) -> CheckoutSession:
        frontend = settings.FRONTEND_URL.rstrip("/")

        # Stripe requires the smallest currency unit (e.g., cents for USD).
        # Passing 800 for ₹800 would result in a charge of ₹8.
        minor_units = int(round(amount * 100))

        data = {
            "mode": "payment",
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": settings.CURRENCY.lower(),
            "line_items[0][price_data][unit_amount]": str(minor_units),
            "line_items[0][price_data][product_data][name]": description,
            # Success URL is for UI flow only; final confirmation relies on webhooks.
            "success_url": f"{frontend}/payment/return?payment_id={payment_id}",
            "cancel_url": f"{frontend}/payment/return?payment_id={payment_id}&cancelled=1",
            # Store payment_id in metadata for easy lookup in webhooks.
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
            logger.warning("Stripe checkout creation failed: %s", exc)
            raise PaymentError("Failed to communicate with payment gateway") from exc

        body = res.json()
        return CheckoutSession(reference=body["id"], url=body["url"])

    def verify_webhook(self, payload: bytes, signature: str | None) -> dict:
        """
        Verify webhook signature. Stripe can't authenticate via our normal
        headers, so the signature is the only auth mechanism here.

        Header format: Stripe-Signature: t=1712345678,v1=abc123...,v1=def456...
        Verification: signed_payload = "{timestamp}.{raw body}", then
        compare HMAC-SHA256(webhook_secret, signed_payload) against the
        provided v1 signatures.
        """
        if not signature:
            raise PaymentError("Signature header missing")

        parts = dict(
            piece.split("=", 1) for piece in signature.split(",") if "=" in piece
        )
        timestamp = parts.get("t")
        if not timestamp:
            raise PaymentError("Signature missing timestamp")

        # Replay protection: Ensure the webhook is recent.
        if abs(time.time() - int(timestamp)) > WEBHOOK_TOLERANCE_SECONDS:
            raise PaymentError("Webhook timestamp expired")

        signed = f"{timestamp}.".encode() + payload
        expected = hmac.new(
            settings.STRIPE_WEBHOOK_SECRET.encode(), signed, hashlib.sha256
        ).hexdigest()

        # Extract all v1 signatures (supports secret rotation).
        provided = [v for k, v in (p.split("=", 1) for p in signature.split(",") if "=" in p) if k == "v1"]

        # Use compare_digest to prevent timing attacks.
        if not any(hmac.compare_digest(expected, got) for got in provided):
            raise PaymentError("Signature mismatch")

        import json

        return json.loads(payload)


# ---------------------------------------------------------------------------

def get_provider():
    """Returns StripeProvider if keys are configured, otherwise MockProvider."""
    return StripeProvider() if settings.payment_provider == "stripe" else MockProvider()
