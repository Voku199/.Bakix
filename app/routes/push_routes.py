import json
import logging
import os

from flask import Blueprint, jsonify, request, session

from app.database.connection import get_connection

log = logging.getLogger(__name__)

push_bp = Blueprint("push", __name__)

VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY  = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_MAILTO      = os.getenv("VAPID_MAILTO", "mailto:admin@example.com")


@push_bp.route("/api/push/vapid-public-key")
def vapid_public_key():
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})


@push_bp.route("/api/push/subscribe", methods=["POST"])
def subscribe():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    ua  = request.headers.get("User-Agent", "—")[:120]
    sub = request.get_json(silent=True)

    if not sub or not sub.get("endpoint"):
        log.warning("subscribe: missing endpoint — user=%.8s ua=%s body=%.80s",
                    user_id, ua, str(sub))
        return jsonify({"error": "Invalid subscription object"}), 400

    endpoint = sub["endpoint"]
    keys     = sub.get("keys") or {}

    # p256dh and auth are required for encrypted payload delivery.
    # iOS APNs and all modern push services always supply them.
    if not keys.get("p256dh") or not keys.get("auth"):
        log.warning(
            "subscribe: missing encryption keys — user=%.8s ua=%s "
            "p256dh=%s auth=%s endpoint=%.50s",
            user_id, ua, bool(keys.get("p256dh")), bool(keys.get("auth")), endpoint,
        )
        return jsonify({"error": "Subscription missing encryption keys"}), 400

    # Identify the push delivery service for operational diagnostics
    service = (
        "APNs"    if "apple.com"                 in endpoint else
        "FCM"     if "googleapis.com"            in endpoint else
        "Mozilla" if "mozilla.com"               in endpoint else
        "other"
    )

    try:
        sub_json = json.dumps(sub)
    except (TypeError, ValueError) as exc:
        log.error("subscribe: JSON serialization error — user=%.8s %s", user_id, exc)
        return jsonify({"error": "Invalid subscription data"}), 400

    with get_connection() as db:
        db.execute("""
            INSERT INTO push_subscriptions (user_id, endpoint, sub_json)
            VALUES (?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                user_id  = excluded.user_id,
                sub_json = excluded.sub_json
        """, (user_id, endpoint, sub_json))

    log.info(
        "push sub saved: user=%.8s service=%s endpoint=%.50s… ua=%s",
        user_id, service, endpoint, ua,
    )
    return jsonify({"ok": True})


@push_bp.route("/api/push/unsubscribe", methods=["POST"])
def unsubscribe():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    endpoint = body.get("endpoint")
    if endpoint:
        _delete_subscription(endpoint)

    return jsonify({"ok": True})


@push_bp.route("/api/push/send", methods=["POST"])
def send_push():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    body    = request.get_json(silent=True) or {}
    title   = body.get("title", "Bakix")
    message = body.get("body", "Testovací notifikace ✦")

    with get_connection() as db:
        rows = db.execute(
            "SELECT endpoint, sub_json FROM push_subscriptions WHERE user_id = ?",
            (user_id,),
        ).fetchall()

    if not rows:
        return jsonify({"ok": False, "error": "no_subscriptions"}), 404

    if not VAPID_PRIVATE_KEY:

        return jsonify({"ok": False, "error": "VAPID not configured"}), 503

    from pywebpush import webpush, WebPushException

    sent = 0
    for row in rows:
        try:
            webpush(
                subscription_info=json.loads(row["sub_json"]),
                data=json.dumps({"title": title, "body": message}),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_MAILTO},
                content_encoding="aes128gcm",  # RFC 8291; required by iOS APNs
                ttl=86400,                     # APNs drops messages with ttl=0
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None) if exc.response else None
            log.warning("push failed endpoint=%.40s… status=%s", row["endpoint"], status)
            if status in (404, 410):
                _delete_subscription(row["endpoint"])

    return jsonify({"ok": True, "sent": sent})


def _delete_subscription(endpoint: str) -> None:
    with get_connection() as db:
        db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    log.info("push sub deleted: endpoint=%.40s…", endpoint)
