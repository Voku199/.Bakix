"""User-facing settings endpoints: subscription, cache, app + profile settings."""

import logging

from flask import session, request, jsonify, current_app
from flask_babel import gettext as _

from app.database.db import fetch_row, get_settings as _db_get_settings, save_settings as _db_save_settings, upsert_all, update_display_name as _db_update_display_name
from app.extensions import limiter
from app.routes.bakalari import bakalari_bp
from app.services.bakalari import BakalariService
from app.services.crypto import encrypt_json

log = logging.getLogger(__name__)

@bakalari_bp.route("/api/subscription", methods=["GET"])
@limiter.limit("30 per minute")
def api_subscription_get():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    from app.database.db import get_subscription_info
    return jsonify(get_subscription_info(user_id))


@bakalari_bp.route("/api/subscription", methods=["POST"])
def api_subscription_post():
    """Dev-only manual tier toggle.

    Premium is now sold through Stripe (see /api/payment/checkout). Granting it
    for free is gated behind DEBUG so production can't self-upgrade — real users
    must pay. 'cancel' (downgrade to free) stays allowed everywhere.
    """
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    data   = request.get_json(force=True, silent=True) or {}
    action = (data.get("action") or "").strip()
    if action not in ("upgrade", "cancel"):
        return jsonify({"error": "Invalid action"}), 400

    if action == "upgrade":
        if not current_app.config.get("DEBUG"):
            return jsonify({"error": "Premium se aktivuje platbou.",
                            "checkout": "/api/payment/checkout"}), 403
        from app.database.db import grant_premium_days
        grant_premium_days(user_id, 30)  # dev shortcut: +30 days
        return jsonify({"ok": True, "tier": "premium"})

    from app.database.db import update_subscription_tier
    update_subscription_tier(user_id, "free")
    return jsonify({"ok": True, "tier": "free"})


@bakalari_bp.route("/api/cache/clear", methods=["POST"])
@limiter.limit("10 per minute")
def api_cache_clear():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    from app.database.db import cache_clear
    cleared = cache_clear(user_id)
    return jsonify({"ok": True, "cleared": cleared})


@bakalari_bp.route("/api/settings", methods=["GET"])
@limiter.limit("30 per minute")
def api_settings_get():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    return jsonify(_db_get_settings(user_id))


@bakalari_bp.route("/api/settings", methods=["POST"])
@limiter.limit("10 per minute")
def api_settings_post():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    data       = request.get_json(force=True, silent=True) or {}
    school_url = data.pop("school_url", "").strip().rstrip("/")
    username   = data.pop("username",   "").strip()
    password   = data.pop("password",   "").strip()

    if password:
        if school_url and not school_url.startswith(("http://", "https://")):
            school_url = f"https://{school_url}"
        if not school_url:
            row = fetch_row(user_id)
            school_url = row["school_url"] if row else ""
        svc    = BakalariService(base_url=school_url)
        result = svc.login(username, password)
        if "error" in result:
            return jsonify({"error": result.get("error", _("Přihlášení selhalo."))}), 401
        enc = encrypt_json({"username": username, "password": password})
        upsert_all(
            user_id=user_id,
            school_url=school_url,
            enc_creds=enc,
            access_token=result["access_token"],
            refresh_token=result["refresh_token"],
        )

    language_changed = False
    new_lang = data.get("language", "")
    if new_lang in ("cs", "en") and session.get("language") != new_lang:
        session["language"] = new_lang
        session.modified = True
        language_changed = True
        log.debug("api_settings_post: session language set to %s for user=%.8s", new_lang, user_id)

    _db_save_settings(user_id, data)
    return jsonify({"ok": True, "language_changed": language_changed})



@bakalari_bp.route("/api/user/settings", methods=["POST"])
@limiter.limit("10 per minute")
def api_user_settings_post():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    data         = request.get_json(force=True, silent=True) or {}
    display_name = (data.get("display_name") or "").strip()[:80]

    if not display_name:
        return jsonify({"error": "display_name is required"}), 400

    _db_update_display_name(user_id, display_name)
    return jsonify({"ok": True, "display_name": display_name})

