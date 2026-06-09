import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import requests as _http
from flask import Blueprint, jsonify, request

from app.extensions import limiter
from app.services.bakalari import BakalariService

log = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

_MUNI_BASE = "https://sluzby.bakalari.cz/api/v1/municipality"
_HDRS      = {"Accept": "application/json"}
_TTL       = 86_400  # 24 h

_lock  = threading.Lock()
_cache: dict = {
    "cities":     None,
    "cities_exp": 0.0,
    "schools":    {},   # city -> list[dict]
    "school_exp": {},   # city -> float
}


def _municipalities() -> list:
    now = time.monotonic()
    with _lock:
        if _cache["cities"] and _cache["cities_exp"] > now:
            return _cache["cities"]
    try:
        r = _http.get(_MUNI_BASE, headers=_HDRS, timeout=8)
        r.raise_for_status()
        cities = [m["name"] for m in r.json() if m.get("name")]
    except Exception:
        log.warning("schools: failed to fetch municipality list")
        return _cache.get("cities") or []
    with _lock:
        _cache["cities"]     = cities
        _cache["cities_exp"] = now + _TTL
    return cities


def _schools_for_city(city: str) -> list:
    now = time.monotonic()
    with _lock:
        if city in _cache["schools"] and _cache["school_exp"].get(city, 0.0) > now:
            return _cache["schools"][city]

    encoded = quote(city, safe="")
    schools: list = []
    try:
        r = _http.get(f"{_MUNI_BASE}/{encoded}", headers=_HDRS, timeout=6)
        if r.status_code == 404 and "." in city:
            encoded = quote(city.split(".")[0], safe="")
            r = _http.get(f"{_MUNI_BASE}/{encoded}", headers=_HDRS, timeout=6)
        r.raise_for_status()
        schools = [
            {"name": s["name"], "city": city, "url": s["schoolUrl"].rstrip("/")}
            for s in r.json().get("schools", [])
            if s.get("name") and s.get("schoolUrl")
        ]
    except Exception:
        log.debug("schools: failed to fetch city=%s", city)

    with _lock:
        _cache["schools"][city]    = schools
        _cache["school_exp"][city] = now + _TTL
    return schools


# ── School search ─────────────────────────────────────────────────────────────

@auth_bp.route("/api/schools/search")
@limiter.limit("30 per minute")
def schools_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])

    q_lo    = q.lower()
    cities  = _municipalities()
    matched = [c for c in cities if q_lo in c.lower()][:6]

    if not matched:
        return jsonify([])

    results: list = []
    with ThreadPoolExecutor(max_workers=len(matched)) as pool:
        for schools in pool.map(_schools_for_city, matched):
            results.extend(schools)

    results.sort(key=lambda s: s["city"])
    return jsonify(results[:40])


# ── School URL validation ──────────────────────────────────────────────────────

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
