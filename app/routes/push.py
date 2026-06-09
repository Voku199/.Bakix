"""
Push notification blueprint.

Setup — VAPID key generation:
    pip install pywebpush
    python - <<'EOF'
from py_vapid import Vapid
import base64, json
v = Vapid()
v.generate_keys()
priv = base64.urlsafe_b64encode(
    v.private_key.private_numbers().private_value.to_bytes(32, "big")
).rstrip(b"=").decode()
pub = base64.urlsafe_b64encode(
    v.public_key.public_bytes(
        __import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding","PublicFormat"]).Encoding.X962,
        __import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding","PublicFormat"]).PublicFormat.UncompressedPoint,
    )
).rstrip(b"=").decode()
print(f"VAPID_PRIVATE_KEY={priv}")
print(f"VAPID_PUBLIC_KEY={pub}")
EOF

Add to .env:
    VAPID_PRIVATE_KEY=<base64url-encoded private scalar>
    VAPID_PUBLIC_KEY=<base64url-encoded uncompressed public point>
    VAPID_MAILTO=mailto:you@example.com
"""

import json
import logging
import os

from flask import Blueprint, current_app, jsonify, request, session

from app.database.connection import get_connection

log = logging.getLogger(__name__)

push_bp = Blueprint("push", __name__)

VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY  = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_MAILTO      = os.getenv("VAPID_MAILTO", "mailto:admin@example.com")


# ── Public key endpoint (no auth required) ────────────────────────────────

@push_bp.route("/api/push/vapid-public-key")
def vapid_public_key():
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})


# ── Subscribe ─────────────────────────────────────────────────────────────

@push_bp.route("/api/push/subscribe", methods=["POST"])
def subscribe():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    ua  = request.headers.get("User-Agent", "—")[:120]
    sub = request.get_json(silent=True)

    if not sub or not sub.get("endpoint"):
        log.warning("push/subscribe: missing endpoint user=%.8s ua=%s", user_id, ua)
        return jsonify({"error": "Invalid subscription object"}), 400

    endpoint    = sub["endpoint"]
    keys        = sub.get("keys") or {}
    keys_auth   = (keys.get("auth")   or "").strip()
    keys_p256dh = (keys.get("p256dh") or "").strip()

    # Both keys are required for encrypted payload delivery.
    # Every modern push service (APNs, FCM, Mozilla) always provides them.
    if not keys_auth or not keys_p256dh:
        log.warning(
            "push/subscribe: missing encryption keys "
            "user=%.8s p256dh=%s auth=%s endpoint=%.50s ua=%s",
            user_id, bool(keys_p256dh), bool(keys_auth), endpoint, ua,
        )
        return jsonify({"error": "Subscription missing encryption keys"}), 400

    service = (
        "APNs"    if "apple.com"      in endpoint else
        "FCM"     if "googleapis.com" in endpoint else
        "Mozilla" if "mozilla.com"    in endpoint else
        "other"
    )

    with get_connection() as db:
        db.execute(
            """
            INSERT INTO push_subscriptions (user_id, endpoint, keys_auth, keys_p256dh)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                user_id     = excluded.user_id,
                keys_auth   = excluded.keys_auth,
                keys_p256dh = excluded.keys_p256dh
            """,
            (user_id, endpoint, keys_auth, keys_p256dh),
        )

    log.info(
        "push/subscribe: saved user=%.8s service=%s endpoint=%.50s ua=%s",
        user_id, service, endpoint, ua,
    )
    return jsonify({"ok": True})


# ── Unsubscribe ───────────────────────────────────────────────────────────

@push_bp.route("/api/push/unsubscribe", methods=["POST"])
def unsubscribe():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    body     = request.get_json(silent=True) or {}
    endpoint = body.get("endpoint")
    if endpoint:
        _delete_subscription(endpoint)
    return jsonify({"ok": True})


# ── Send (triggers push to all subscriptions of the current user) ─────────

@push_bp.route("/api/push/send", methods=["POST"])
def send_push():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    body    = request.get_json(silent=True) or {}
    title   = body.get("title", "Bakix")
    message = body.get("body",  "Testovaci notifikace")

    with get_connection() as db:
        rows = db.execute(
            "SELECT endpoint, keys_auth, keys_p256dh "
            "FROM push_subscriptions WHERE user_id = ?",
            (user_id,),
        ).fetchall()

    if not rows:
        return jsonify({"ok": False, "error": "no_subscriptions"}), 404

    if not VAPID_PRIVATE_KEY:
        log.error("push/send: VAPID_PRIVATE_KEY not set")
        return jsonify({"ok": False, "error": "VAPID not configured"}), 503

    from pywebpush import webpush, WebPushException

    sent = 0
    for row in rows:
        sub_info = {
            "endpoint": row["endpoint"],
            "keys": {
                "auth":   row["keys_auth"],
                "p256dh": row["keys_p256dh"],
            },
        }
        try:
            webpush(
                subscription_info=sub_info,
                data=json.dumps({"title": title, "body": message, "url": "/"}),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_MAILTO},
                content_encoding="aes128gcm",   # RFC 8291; required by iOS APNs
                ttl=86400,                       # non-zero TTL required by APNs
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None) if exc.response else None
            log.warning(
                "push/send: failed endpoint=%.50s status=%s",
                row["endpoint"], status,
            )
            if status in (404, 410):   # subscription expired or revoked
                _delete_subscription(row["endpoint"])

    log.info("push/send: user=%.8s sent=%d/%d", user_id, sent, len(rows))
    return jsonify({"ok": True, "sent": sent})


# ── Debug test triggers (DEBUG mode only) ────────────────────────────────

_DEBUG_PAYLOADS = {
    "grade": (
        "Nová známka v Bakixu",
        "1 z Matematiky – písemka, kapitola 3",
        "/#marks-body",
    ),
    "homework": (
        "Nový úkol v Bakixu",
        "Cvičení z Angličtiny – odevzdat do pozítří",
        "/#homeworks-body",
    ),
    "komens": (
        "Nová zpráva v Bakixu",
        "Třídní učitel: Pozvánka na třídní schůzky",
        "/#komens-body",
    ),
}


@push_bp.route("/api/debug/push/<push_type>", methods=["POST"])
def debug_push(push_type):
    if not current_app.config.get("DEBUG"):
        return jsonify({"error": "Not available outside DEBUG mode"}), 403

    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    if push_type not in _DEBUG_PAYLOADS:
        return jsonify({"error": "Unknown push type", "valid": list(_DEBUG_PAYLOADS)}), 400

    from app.services.push_service import PushNotificationService
    title, body, url = _DEBUG_PAYLOADS[push_type]
    sent = PushNotificationService().send_to_user(user_id, title, body, url=url)
    log.info("debug_push: type=%s user=%.8s sent=%d", push_type, user_id, sent)
    return jsonify({"ok": True, "sent": sent})


# ── Internal helper ───────────────────────────────────────────────────────

def _delete_subscription(endpoint: str) -> None:
    with get_connection() as db:
        db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    log.info("push/subscribe: deleted endpoint=%.50s", endpoint)
