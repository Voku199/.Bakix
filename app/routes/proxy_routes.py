"""
proxy_routes.py — Routes mirrored from PROXY_Bakix for feature parity.

These endpoints expose the same data shapes as the external PROXY_Bakix service
but are integrated directly into .Bakix and use session-based auth instead of
Bearer tokens + X-School-Url headers.

New endpoints
─────────────
  GET  /api/ping
  GET  /api/dashboard/homeworks/count
  GET  /api/dashboard/homeworks          ?from=&to=&done=
  GET  /api/dashboard/marks              (flat list, includes mark_id / type_note / theme)
  GET  /api/dashboard/marks/theme        ?subject_id=&mark_date=
  GET  /api/dashboard/messages/unread
  POST /api/dashboard/messages/<id>/read
"""

import logging
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, request, session

from app.database.db import cache_get, cache_set, fetch_row

log = logging.getLogger(__name__)

proxy_bp = Blueprint("proxy", __name__)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _get_svc_and_token():
    """Return (BakalariService, access_token, user_id) from session."""
    user_id = session.get("user_id")
    if not user_id:
        return None, None, None
    row = fetch_row(user_id)
    if not row:
        return None, None, None
    from app.services.bakalari import BakalariService
    svc = BakalariService(base_url=row["school_url"])
    return svc, svc.get_token(user_id), user_id


def _pick_theme_for_mark(themes: list, mark_date_str: str) -> "dict | None":
    """Return the curriculum theme closest to *mark_date_str*.

    Prefers themes on or before the mark date (same-day or most-recent prior).
    Falls back to the nearest future theme when no prior theme exists.

    *themes* must be in the normalised ``{"name": str, "date": "YYYY-MM-DD"}``
    format produced by :func:`_normalise_themes`.
    """
    if not mark_date_str:
        return None
    try:
        mark_date = datetime.fromisoformat(
            str(mark_date_str).replace("Z", "+00:00")
        ).date()
    except (ValueError, TypeError):
        return None

    candidates = []
    for item in themes:
        raw = (item.get("date") or "").strip()
        if not raw:
            continue
        try:
            theme_date = datetime.fromisoformat(raw).date()
        except (ValueError, TypeError):
            continue
        # delta > 0 → theme is after the mark; delta ≤ 0 → theme is on/before the mark
        candidates.append(((theme_date - mark_date).days, item))

    if not candidates:
        return None

    on_or_before = [c for c in candidates if c[0] <= 0]
    if on_or_before:
        # max() of non-positive deltas → closest to zero → most recent prior theme
        return max(on_or_before, key=lambda c: c[0])[1]

    # No prior theme — pick the nearest future one
    return min(candidates, key=lambda c: c[0])[1]


def _normalise_themes(themes_raw: list) -> list:
    """Convert raw Bakaláři theme objects to ``{"name": str, "date": str}`` dicts."""
    out = []
    for t in themes_raw:
        if not isinstance(t, dict):
            continue
        name = t.get("Title") or t.get("Name") or t.get("name") or ""
        d    = (t.get("Date") or t.get("date") or "")[:10]
        if name:
            out.append({"name": name, "date": d})
    return out


# ── Health check ──────────────────────────────────────────────────────────────


@proxy_bp.route("/api/ping")
def ping():
    return jsonify({"ok": True})


# ── Homework routes ───────────────────────────────────────────────────────────


@proxy_bp.route("/api/dashboard/homeworks/count")
def api_dashboard_homeworks_count():
    """Return the count of pending (not-done, not-closed) homeworks for the next 30 days.

    Response 200:
        {"count": int}
    """
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

        today  = date.today()
        future = (today + timedelta(days=30)).isoformat()
        ck     = f"hw_count_{today.isoformat()}"

        hit = cache_get(user_id, ck)
        if hit is not None:
            return jsonify(hit)

        data = svc.get_homeworks(token, today.isoformat(), future)
        if data.get("status_code") == 401:
            token = svc.reauth(user_id)
            if not token:
                return jsonify({"error": "Not authenticated"}), 401
            data = svc.get_homeworks(token, today.isoformat(), future)

        if "error" in data:
            return jsonify({"error": f"Nepodařilo se načíst úkoly ({data.get('status_code', '')})"}), 502

        count  = sum(
            1 for hw in (data.get("Homeworks") or [])
            if not hw.get("Done") and not hw.get("Closed")
        )
        result = {"count": count}
        cache_set(user_id, ck, result)
        return jsonify(result)

    except Exception:
        log.exception("api_dashboard_homeworks_count: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@proxy_bp.route("/api/dashboard/homeworks")
def api_dashboard_homeworks():
    """Richer homework list with flexible date range and done/undone filter.

    Compared to /api/3/homeworks this endpoint:
      - Accepts ``from`` / ``to`` query params (default: today … today + 30 days)
      - Accepts a ``done`` filter (``true`` | ``false`` | absent → all)
      - Returns additional fields: ``subject_abbr``, ``teacher``, ``class``, ``closed``

    Query params:
        from   YYYY-MM-DD   (default: today)
        to     YYYY-MM-DD   (default: today + 30 days)
        done   true|false   (default: no filter)

    Response 200:
        {"homeworks": [...], "count": int, "from": str, "to": str}
    """
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

        today = date.today()

        def _parse_date(val, fallback):
            try:
                return date.fromisoformat(val)
            except (ValueError, TypeError, AttributeError):
                return fallback

        from_date = _parse_date(request.args.get("from"), today)
        to_date   = _parse_date(request.args.get("to"),   today + timedelta(days=30))
        from_str  = from_date.isoformat()
        to_str    = to_date.isoformat()

        ck  = f"hw_full_{from_str}_{to_str}"
        hit = cache_get(user_id, ck)

        if hit is not None:
            hw_list = hit
        else:
            data = svc.get_homeworks(token, from_str, to_str)
            if data.get("status_code") == 401:
                token = svc.reauth(user_id)
                if not token:
                    return jsonify({"error": "Not authenticated"}), 401
                data = svc.get_homeworks(token, from_str, to_str)

            if "error" in data:
                return jsonify({"error": f"Nepodařilo se načíst úkoly ({data.get('status_code', '')})"}), 502

            hw_list = [
                {
                    "id":           hw.get("Id") or hw.get("ID"),
                    "date_start":   (hw.get("DateStart") or "")[:10],
                    "date_end":     (hw.get("DateEnd")   or "")[:10],
                    "done":         bool(hw.get("Done")),
                    "closed":       bool(hw.get("Closed")),
                    "content":      (hw.get("Content")  or ""),
                    "subject":      (hw.get("Subject")  or {}).get("Name",   ""),
                    "subject_abbr": (hw.get("Subject")  or {}).get("Abbrev", ""),
                    "teacher":      (hw.get("Teacher")  or {}).get("Name",   ""),
                    "class":        (hw.get("Class")    or {}).get("Abbrev", ""),
                }
                for hw in (data.get("Homeworks") or [])
            ]
            hw_list.sort(key=lambda h: h["date_end"])
            cache_set(user_id, ck, hw_list)

        # Apply done filter *after* cache so the cached list is always unfiltered
        done_param = request.args.get("done")
        if done_param == "false":
            hw_list = [h for h in hw_list if not h["done"]]
        elif done_param == "true":
            hw_list = [h for h in hw_list if h["done"]]

        return jsonify({"homeworks": hw_list, "count": len(hw_list),
                        "from": from_str, "to": to_str})

    except Exception:
        log.exception("api_dashboard_homeworks: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


# ── Marks routes ──────────────────────────────────────────────────────────────


@proxy_bp.route("/api/dashboard/marks")
def api_dashboard_marks():
    """Flat list of all marks (latest 100, newest first).

    Unlike /api/3/marks (which groups by subject), this endpoint returns a
    flat array with additional fields not exposed by the grouped endpoint:
    ``mark_id``, ``type_note``, ``theme``, ``subject_id``, ``date`` (MarkDate).

    Response 200:
        {"marks": [{subject, subject_id, mark, caption, date, weight,
                    mark_id, type_note, theme, is_points}, ...]}
    """
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

        ck  = "marks_flat"
        hit = cache_get(user_id, ck)
        if hit is not None:
            return jsonify(hit)

        data = svc.get_marks(token)
        if data.get("status_code") == 401:
            token = svc.reauth(user_id)
            if not token:
                return jsonify({"error": "Not authenticated"}), 401
            data = svc.get_marks(token)

        if "error" in data:
            return jsonify({"error": f"Nepodařilo se načíst známky ({data.get('status_code', '')})"}), 502

        all_marks = []
        for subject in (data.get("Subjects") if isinstance(data, dict) else []) or []:
            subj    = subject.get("Subject") or {}
            subj_nm = subj.get("Name")
            subj_id = subj.get("Id")
            for mark in (subject.get("Marks") or []):
                all_marks.append({
                    "subject":    subj_nm,
                    "subject_id": subj_id,
                    "mark":       mark.get("MarkText"),
                    "caption":    mark.get("Caption"),
                    # MarkDate is the display date; fall back to EditDate
                    "date":       (mark.get("MarkDate") or mark.get("EditDate") or "")[:10],
                    "weight":     mark.get("Weight"),
                    "mark_id":    mark.get("Id"),
                    "type_note":  mark.get("TypeNote"),
                    "theme":      mark.get("Theme"),
                    "is_points":  bool(mark.get("IsPoints")),
                })

        all_marks.sort(key=lambda m: m["date"], reverse=True)
        result = {"marks": all_marks[:100]}
        cache_set(user_id, ck, result)
        return jsonify(result)

    except Exception:
        log.exception("api_dashboard_marks: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@proxy_bp.route("/api/dashboard/marks/theme")
def api_dashboard_marks_theme():
    """Return curriculum themes for a subject, with smart date-matching.

    When ``mark_date`` is supplied the response includes ``selected_theme``:
    the theme closest to (and on or before) that date, which is the most likely
    topic tested by a mark on that day.  Mirrors ``_pick_theme_for_mark`` from
    PROXY_Bakix.

    Query params:
        subject_id   str           (required) — e.g. "MAT"
        mark_date    YYYY-MM-DD    (optional)

    Response 200:
        {"subject_id": str, "selected_theme": dict|null, "themes": [...]}
    """
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

        subject_id = (request.args.get("subject_id") or "").strip()
        mark_date  = (request.args.get("mark_date")  or "").strip()

        if not subject_id:
            return jsonify({"error": "Missing query param: subject_id"}), 400

        # Share the same cache key as the existing /api/3/subjects/themes/<id>
        # endpoint so both use one cached fetch (7-day TTL).
        ck  = f"themes_{subject_id}"
        hit = cache_get(user_id, ck, ttl=604_800)

        if hit is not None:
            themes = hit   # already in {"name": str, "date": str} format
        else:
            result = svc.get_subject_themes(token, subject_id)
            if result.get("status_code") == 401:
                token = svc.reauth(user_id)
                if not token:
                    return jsonify({"error": "Not authenticated"}), 401
                result = svc.get_subject_themes(token, subject_id)

            if "error" in result:
                code = result.get("status_code", 502)
                return jsonify(result), code

            themes = _normalise_themes(
                result.get("Themes") or result.get("themes") or []
            )
            cache_set(user_id, ck, themes)

        selected = _pick_theme_for_mark(themes, mark_date) if mark_date else None
        return jsonify({"subject_id": subject_id, "selected_theme": selected,
                        "themes": themes})

    except Exception:
        log.exception("api_dashboard_marks_theme: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


# ── Messages routes ───────────────────────────────────────────────────────────


@proxy_bp.route("/api/dashboard/messages/unread")
def api_dashboard_messages_unread():
    """Return the count and list of unread Komens messages.

    Unlike /api/3/komens/messages/received (POST, all messages, session cache),
    this endpoint:
      - Uses GET (bookmarkable / fetchable without a body)
      - Filters to unread messages only
      - Returns the raw Bakaláří message objects (not stripped/mapped)

    Response 200:
        {"count": int, "messages": [...]}
    """
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

        ck  = "komens_unread"
        hit = cache_get(user_id, ck)
        if hit is not None:
            return jsonify(hit)

        data = svc.get_komens(token)
        if data.get("status_code") == 401:
            token = svc.reauth(user_id)
            if not token:
                return jsonify({"error": "Not authenticated"}), 401
            data = svc.get_komens(token)

        if "error" in data:
            return jsonify({"error": f"Nepodařilo se načíst zprávy ({data.get('status_code', '')})"}), 502

        unread = [m for m in (data.get("Messages") or []) if not m.get("Read", True)]
        result = {"count": len(unread), "messages": unread}
        cache_set(user_id, ck, result)
        return jsonify(result)

    except Exception:
        log.exception("api_dashboard_messages_unread: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@proxy_bp.route("/api/dashboard/messages/<message_id>/read", methods=["POST"])
def api_dashboard_messages_mark_read(message_id):
    """Mark a Komens message as read via /api/3/komens/messages/read.

    Invalidates the komens and komens_unread caches so subsequent reads
    reflect the updated state.

    Response 200:
        {"ok": true}
    Response 404:
        {"error": "Message not found", "status_code": 404}
    """
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

        result = svc.mark_message_read(token, message_id)
        if result.get("status_code") == 401:
            token = svc.reauth(user_id)
            if not token:
                return jsonify({"error": "Not authenticated"}), 401
            result = svc.mark_message_read(token, message_id)

        if "error" in result:
            return jsonify(result), result.get("status_code", 502)

        # Invalidate both komens caches: cache_set(…, None) stores JSON null,
        # which cache_get returns as None → treated as a cache miss on next read.
        cache_set(user_id, "komens",        None)
        cache_set(user_id, "komens_unread", None)

        return jsonify({"ok": True})

    except Exception:
        log.exception("api_dashboard_messages_mark_read: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500
