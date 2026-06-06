"""
Payment service — Stripe Checkout for Bakix Premium.

Flow (one-time payment, manual renewal):
    1. User clicks "Aktivovat Premium" → POST /api/payment/checkout
       → create_checkout_session() makes a Stripe Checkout Session and we send
         the user to Stripe's hosted page.
    2. User pays with a card. For TEST mode use card 4242 4242 4242 4242,
       any future expiry, any CVC, any ZIP.
    3. Stripe redirects back to /payment/success?session_id=...
       → verify_and_fulfil() asks Stripe whether the session was paid and, if so,
         grants 30 days of premium (idempotently).
    4. (Optional, production) Stripe also POSTs to /api/payment/webhook — the same
       fulfilment runs there, so payment is credited even if the user closes the
       browser before the redirect.

TEST vs LIVE is decided purely by the key prefix:
    sk_test_... → sandbox (no real money, no IČO needed)
    sk_live_... → real charges (needs a verified Stripe account + IČO)

Setup:
    pip install stripe
    Add to .env (test keys from https://dashboard.stripe.com/test/apikeys):
        STRIPE_SECRET_KEY=sk_test_...
        STRIPE_PUBLISHABLE_KEY=pk_test_...
        STRIPE_WEBHOOK_SECRET=whsec_...      # only needed for the webhook
        PREMIUM_PRICE_CZK=50
        PREMIUM_DAYS=30
"""

import logging
import os

log = logging.getLogger(__name__)

# Graceful optional import — the app must still boot if `stripe` isn't installed
# (matches the try/except pattern used elsewhere in the project).
try:
    import stripe as _stripe
except ImportError:  # pragma: no cover
    _stripe = None
    log.warning("payment_service: 'stripe' not installed — payments disabled "
                "(pip install stripe)")


# ── Config (read lazily so .env is loaded first) ──────────────────────────────

def _secret_key() -> str:
    return os.getenv("STRIPE_SECRET_KEY", "").strip()


def premium_price_czk() -> int:
    try:
        return int(os.getenv("PREMIUM_PRICE_CZK", "50"))
    except ValueError:
        return 50


def premium_days() -> int:
    try:
        return int(os.getenv("PREMIUM_DAYS", "30"))
    except ValueError:
        return 30


def is_configured() -> bool:
    """True when Stripe can be used (library present + secret key set)."""
    return _stripe is not None and bool(_secret_key())


def is_test_mode() -> bool:
    """True when running against Stripe's sandbox (test keys / not configured)."""
    return not _secret_key().startswith("sk_live_")


def publishable_key() -> str:
    return os.getenv("STRIPE_PUBLISHABLE_KEY", "").strip()


class PaymentError(Exception):
    """Raised for any recoverable payment failure (shown to the user)."""


def _client():
    if not is_configured():
        raise PaymentError(
            "Platby zatím nejsou nastavené. Doplň STRIPE_SECRET_KEY do .env "
            "(testovací klíč sk_test_… získáš zdarma na dashboard.stripe.com)."
        )
    _stripe.api_key = _secret_key()
    return _stripe


# ── Checkout ──────────────────────────────────────────────────────────────────

def create_checkout_session(user_id: str, base_url: str) -> dict:
    """Create a one-time Stripe Checkout Session for Premium.

    `base_url` is the public origin (e.g. https://bakix.cz/ or the dev host),
    used to build the success/cancel redirect URLs.
    Returns {"id": session_id, "url": hosted_checkout_url}.
    """
    stripe = _client()
    price  = premium_price_czk()
    days   = premium_days()
    origin = base_url.rstrip("/")

    try:
        cs = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[{
                "quantity": 1,
                "price_data": {
                    "currency": "czk",
                    # Stripe expects the amount in the smallest unit (haléře).
                    "unit_amount": price * 100,
                    "product_data": {
                        "name": "Bakix Premium",
                        "description": f"{days} dní Premium — 50 AI dotazů denně",
                    },
                },
            }],
            metadata={"user_id": user_id, "days": str(days)},
            # session_id placeholder is filled in by Stripe on redirect.
            success_url=f"{origin}/payment/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{origin}/payment/cancel",
            locale="cs",
        )
    except Exception as exc:  # stripe.error.StripeError and friends
        log.exception("create_checkout_session failed: user=%.8s", user_id)
        raise PaymentError(f"Nepodařilo se zahájit platbu: {exc}") from exc

    # Record the attempt so we can fulfil idempotently later.
    from app.database.db import record_payment_pending
    record_payment_pending(user_id, cs.id, price, days)

    log.info("checkout created: user=%.8s session=%.20s test=%s",
             user_id, cs.id, is_test_mode())
    return {"id": cs.id, "url": cs.url}


# ── Fulfilment ────────────────────────────────────────────────────────────────

def _fulfil(session_id: str, payment_intent: "str | None") -> "str | None":
    """Grant premium for a paid session exactly once.

    Returns the new expiry string if this call performed the grant, else None
    (already fulfilled / unknown session)."""
    from app.database.db import (
        get_payment_by_session, mark_payment_paid, grant_premium_days,
    )
    pay = get_payment_by_session(session_id)
    if not pay:
        log.warning("fulfil: unknown session %.20s", session_id)
        return None
    if not mark_payment_paid(session_id, payment_intent):
        log.info("fulfil: session %.20s already credited — skipping", session_id)
        return None
    return grant_premium_days(pay["user_id"], pay["days_granted"] or premium_days())


def verify_and_fulfil(session_id: str) -> dict:
    """Confirm a checkout session with Stripe and credit premium if paid.

    Called from the success redirect. Safe to call repeatedly — fulfilment is
    idempotent. Returns {"paid": bool, "expires_at": str|None, "tier": str}.
    """
    stripe = _client()
    from app.database.db import get_payment_by_session, get_subscription_info

    pay = get_payment_by_session(session_id)
    if not pay:
        raise PaymentError("Neznámá platba.")

    try:
        cs = stripe.checkout.Session.retrieve(session_id)
    except Exception as exc:
        log.exception("verify: retrieve failed %.20s", session_id)
        raise PaymentError(f"Nepodařilo se ověřit platbu: {exc}") from exc

    # Stripe returns a StripeObject — use getattr (its .get() collides with
    # attribute access and raises). payment_status / payment_intent are always
    # present on a Checkout Session.
    paid = getattr(cs, "payment_status", None) == "paid"
    if paid:
        _fulfil(session_id, getattr(cs, "payment_intent", None))

    info = get_subscription_info(pay["user_id"])
    return {"paid": paid, "expires_at": info["expires_at"], "tier": info["tier"]}


def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """Verify and process a Stripe webhook event. Returns {"ok": bool, ...}.

    Requires STRIPE_WEBHOOK_SECRET. Handles checkout.session.completed by
    fulfilling the matching payment (idempotent with the redirect path)."""
    stripe = _client()
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise PaymentError("STRIPE_WEBHOOK_SECRET není nastaven.")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception as exc:
        log.warning("webhook: signature verification failed: %s", exc)
        raise PaymentError("Neplatný podpis webhooku.") from exc

    etype = getattr(event, "type", None)
    if etype == "checkout.session.completed":
        cs = event["data"]["object"]
        if getattr(cs, "payment_status", None) == "paid":
            _fulfil(cs["id"], getattr(cs, "payment_intent", None))
        return {"ok": True, "handled": etype}

    return {"ok": True, "ignored": etype}
