"""
Payment blueprint — Stripe Checkout for Bakix Premium.

Endpoints:
    POST /api/payment/checkout   → create a Stripe session, return its URL (JSON)
    GET  /payment/success        → Stripe redirect target; verifies + grants premium
    GET  /payment/cancel         → Stripe redirect target when the user backs out
    POST /api/payment/webhook    → (optional) Stripe webhook, fulfils server-side

/api/* paths are exempt from the auth gate (see app/__init__.py), so the webhook
works without a session. /payment/* pages stay behind the gate — the user must be
logged in to land on them.
"""

import logging

from flask import (
    Blueprint, current_app, jsonify, render_template, request, session,
)

from app.extensions import limiter
from app.services import payment_service as pay

log = logging.getLogger(__name__)

payment_bp = Blueprint("payment", __name__)


@payment_bp.route("/api/payment/checkout", methods=["POST"])
@limiter.limit("10 per hour")
def checkout():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Nejste přihlášeni."}), 401
    if not pay.is_configured():
        return jsonify({
            "error": "Platby zatím nejsou aktivní. Zkus to prosím později."
        }), 503
    try:
        result = pay.create_checkout_session(user_id, request.url_root)
    except pay.PaymentError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"url": result["url"]})


@payment_bp.route("/payment/success")
def success():
    user_id    = session.get("user_id")
    session_id = (request.args.get("session_id") or "").strip()
    result = {"paid": False, "expires_at": None}
    error  = None

    if not user_id:
        error = "Nejste přihlášeni."
    elif not session_id:
        error = "Chybí identifikátor platby."
    else:
        try:
            result = pay.verify_and_fulfil(session_id)
        except pay.PaymentError as exc:
            error = str(exc)
        except Exception:
            log.exception("payment success verify failed")
            error = "Platbu se nepodařilo ověřit. Pokud peníze odešly, ozvi se nám."

    return render_template(
        "payment_success.html",
        paid=result.get("paid"),
        expires_at=result.get("expires_at"),
        price=pay.premium_price_czk(),
        days=pay.premium_days(),
        error=error,
    )


@payment_bp.route("/payment/cancel")
def cancel():
    return render_template("payment_cancel.html")


@payment_bp.route("/api/payment/webhook", methods=["POST"])
def webhook():
    sig = request.headers.get("Stripe-Signature", "")
    try:
        result = pay.handle_webhook(request.get_data(), sig)
    except pay.PaymentError as exc:
        log.warning("webhook rejected: %s", exc)
        return jsonify({"error": str(exc)}), 400
    except Exception:
        log.exception("webhook handler crashed")
        return jsonify({"error": "internal"}), 500
    return jsonify(result)
