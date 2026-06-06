import hashlib
import logging
import os

from flask import Blueprint, abort, redirect, request, session, url_for, jsonify

from app.database.db import fetch_row, upsert_all, get_settings as _db_get_settings
from app.extensions import limiter
from app.services.bakalari import BakalariService
from app.services.crypto import decrypt_json, encrypt_json

log = logging.getLogger(__name__)

login_bp = Blueprint("login", __name__)


def _restore_session_language(user_id: str) -> None:
    """Copy the user's saved language preference into the session so _get_locale() picks it up."""
    try:
        prefs = _db_get_settings(user_id)
        lang  = prefs.get("language", "")
        if lang in ("cs", "en"):
            session["language"] = lang
    except Exception:
        pass


@login_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if request.method == "GET":
        from flask import render_template
        return render_template("login.html")

    school_url = request.form.get("school_url", "").strip().rstrip("/")
    username   = request.form.get("username",   "").strip()
    password   = request.form.get("password",   "")

    if not all([school_url, username, password]):
        return jsonify({"error": "Vyplňte všechna pole."}), 400

    if not school_url.startswith(("http://", "https://")):
        school_url = f"https://{school_url}"

    svc    = BakalariService(base_url=school_url)
    result = svc.login(username, password)

    if "error" in result:
        return jsonify({
            "error":  result["error"],
            "detail": result.get("detail", ""),
        }), 401

    user_id = _make_user_id(school_url, username)

    try:
        enc = encrypt_json({"username": username, "password": password})
        upsert_all(
            user_id=user_id,
            school_url=school_url,
            enc_creds=enc,
            access_token=result["access_token"],
            refresh_token=result["refresh_token"],
        )
        log.info("login: credentials persisted for user=%.8s", user_id)
    except Exception:
        log.exception("login: failed to persist credentials for user=%.8s", user_id)

    session.permanent = True
    session["user_id"] = user_id
    _restore_session_language(user_id)

    return redirect(url_for("bakalari.index"))


@login_bp.route("/login/now", methods=["GET", "POST"])
def login_now():
    if os.getenv("DEBUG", "").lower() not in ("1", "true", "yes"):
        abort(404)

    body       = request.get_json(silent=True) or {}
    school_url = (body.get("school_url") or os.getenv("AUTO_LOGIN_URL", "")).strip().rstrip("/")
    username   = (body.get("username")   or os.getenv("AUTO_LOGIN_USER", "")).strip()
    user_id    = body.get("user_id", "").strip()

    if user_id:
        row = fetch_row(user_id)
    elif school_url and username:
        if not school_url.startswith(("http://", "https://")):
            school_url = f"https://{school_url}"
        user_id = _make_user_id(school_url, username)
        row = fetch_row(user_id)
    else:
        return jsonify({"error": "Provide user_id or set AUTO_LOGIN_URL + AUTO_LOGIN_USER in .env"}), 400

    if not row:
        return jsonify({"error": "User not found — log in manually first"}), 404

    try:
        creds = decrypt_json(row["enc_creds"])
    except ValueError:
        return jsonify({
            "error": "SECRET_KEY changed — stored credentials are invalid. Log in once via /login to re-encrypt."
        }), 409

    svc    = BakalariService(base_url=row["school_url"])
    result = svc.login(creds["username"], creds["password"])

    if "error" in result:
        return jsonify({"error": result["error"]}), 401

    upsert_all(
        user_id=user_id,
        school_url=row["school_url"],
        enc_creds=row["enc_creds"],
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
    )
    session.permanent  = True
    session["user_id"] = user_id
    _restore_session_language(user_id)
    return redirect(url_for("bakalari.index"))


DEMO_USER_ID = "demo"


@login_bp.route("/login-demo")
def login_demo():
    session.permanent = True
    session["user_id"] = DEMO_USER_ID
    session["is_demo"] = True
    return redirect(url_for("bakalari.index"))


@login_bp.route("/logout")
def logout():
    user_id = session.get("user_id")
    if user_id:
        try:
            from app.database.connection import get_connection
            with get_connection() as db:
                db.execute("DELETE FROM push_subscriptions WHERE user_id = ?", (user_id,))
        except Exception:
            pass
    session.clear()
    return redirect(url_for("welcome"))


def _make_user_id(school_url: str, username: str) -> str:
    key = f"{school_url.rstrip('/').lower()}:{username.lower()}"
    return hashlib.sha256(key.encode()).hexdigest()
