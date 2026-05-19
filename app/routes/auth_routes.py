import hashlib
import logging

import requests as _http
from flask import Blueprint, request, session, redirect, url_for, jsonify, render_template

from app.database.db import upsert_all
from app.services.bakalari import BakalariService
from app.services.crypto import encrypt_json

log = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

_SCHOOLS_API = "https://sluzby.bakalari.cz/api/v1/school/"


# ── Pages ─────────────────────────────────────────────────────────────────────

@auth_bp.route("/welcome")
def welcome():
    if _is_authenticated():
        return redirect(url_for("bakalari.index"))
    return render_template("welcome.html")


@auth_bp.route("/onboarding")
def onboarding():
    if _is_authenticated():
        return redirect(url_for("bakalari.index"))
    return render_template("onboarding.html")


@auth_bp.route("/auth/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.welcome"))


# ── Auth ──────────────────────────────────────────────────────────────────────

@auth_bp.route("/auth/login", methods=["POST"])
def login():
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
        log.info("Login: credentials persisted for user=%.8s", user_id)
    except Exception:
        log.exception("Login: failed to persist credentials for user=%.8s", user_id)
        # Non-fatal — session can still function without DB persistence

    # Only the opaque reference key goes in the cookie
    session.permanent = True
    session["user_id"] = user_id

    return redirect(url_for("bakalari.index"))


# ── School search & validation ────────────────────────────────────────────────

@auth_bp.route("/api/schools/search")
def schools_search():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify([])

    try:
        resp = _http.get(_SCHOOLS_API, params={"query": query}, timeout=6)
        resp.raise_for_status()
        raw = resp.json()
    except Exception:
        return jsonify([])

    schools = raw if isinstance(raw, list) else raw.get("schools", [])
    return jsonify([
        {
            "name": s.get("name", ""),
            "town": s.get("town", ""),
            "url":  s.get("schoolUrl") or s.get("apiUrl", ""),
        }
        for s in schools
        if s.get("schoolUrl") or s.get("apiUrl")
    ])


@auth_bp.route("/api/validate-school")
def validate_school():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "Missing url parameter."}), 400

    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    valid = BakalariService.validate_school_url(url)
    return jsonify({"valid": valid, "url": url})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_authenticated() -> bool:
    return bool(session.get("user_id"))


def _make_user_id(school_url: str, username: str) -> str:
    key = f"{school_url.rstrip('/').lower()}:{username.lower()}"
    return hashlib.sha256(key.encode()).hexdigest()
