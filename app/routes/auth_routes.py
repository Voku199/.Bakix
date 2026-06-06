import logging

import requests as _http
from flask import Blueprint, request, jsonify

from app.extensions import limiter
from app.services.bakalari import BakalariService

log = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

_SCHOOLS_API = "https://sluzby.bakalari.cz/api/v1/school/"


# ── School search & validation ────────────────────────────────────────────────

@auth_bp.route("/api/schools/search")
@limiter.limit("30 per minute")
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
@limiter.limit("30 per minute")
def validate_school():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "Missing url parameter."}), 400

    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    valid = BakalariService.validate_school_url(url)
    return jsonify({"valid": valid, "url": url})

