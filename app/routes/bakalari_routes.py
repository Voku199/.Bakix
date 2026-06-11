import datetime
import json
import logging
import os
import re
import threading
import uuid

from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify, abort, current_app
from markupsafe import Markup
try:
    import nh3 as _nh3

    # Allowlist must match what the AI is told to emit (see _AI_CHAT_PROMPT):
    # quizzes (input/label/button + data-answer), flashcards (div), definition
    # lists (dl/dt/dd) and inline SVG diagrams. <script> stays disallowed — the
    # quiz-check logic lives in generated_page.html, keyed off data-answer.
    _SANITIZE_TAGS = {
        "h1","h2","h3","h4","h5","h6","p","br","hr","ul","ol","li",
        "strong","em","b","i","u","s","code","pre","blockquote",
        "table","thead","tbody","tr","th","td","caption",
        "a","img","span","div","section","article","header","footer",
        "input","label","button","dl","dt","dd",
        "svg","path","rect","circle","ellipse","line","polygon","polyline","text","g",
    }
    _SANITIZE_ATTRS = {
        "a":        {"href","title","target"},
        "img":      {"src","alt","width","height"},
        "input":    {"type","name","value","checked","disabled"},
        "button":   {"type"},
        "label":    {"for"},
        # SVG — list both camelCase and lowercased forms (HTML parsing lowercases).
        "svg":      {"viewbox","viewBox","xmlns","width","height","fill","stroke"},
        "path":     {"d","fill","stroke","stroke-width","stroke-linecap","stroke-linejoin"},
        "rect":     {"x","y","width","height","rx","ry","fill","stroke","stroke-width"},
        "circle":   {"cx","cy","r","fill","stroke","stroke-width"},
        "ellipse":  {"cx","cy","rx","ry","fill","stroke","stroke-width"},
        "line":     {"x1","y1","x2","y2","stroke","stroke-width"},
        "polygon":  {"points","fill","stroke","stroke-width"},
        "polyline": {"points","fill","stroke","stroke-width"},
        "text":     {"x","y","fill","font-size","text-anchor","font-weight"},
        "g":        {"fill","stroke","stroke-width","transform"},
        "*":        {"class","id","data-answer","data-gp-check"},
    }

    def _sanitize_html(html: str) -> str:
        return _nh3.clean(html, tags=_SANITIZE_TAGS, attributes=_SANITIZE_ATTRS)
except ImportError:
    from markupsafe import escape as _escape

    def _sanitize_html(html: str) -> str:
        # Fail closed: a regex "sanitizer" is bypassable (e.g. onload= without a
        # space, entity-encoded handlers), so without nh3 we don't pretend to
        # clean — we escape everything and render the AI output as inert text.
        log.error("nh3 not installed — AI HTML rendered as escaped text (install nh3)")
        return str(_escape(html))

from app.database.db import fetch_row, get_settings as _db_get_settings, save_settings as _db_save_settings, upsert_all, update_display_name as _db_update_display_name, cache_get, cache_set
from app.extensions import limiter
from app.services.bakalari import BakalariService
from app.services.crypto import encrypt_json
from app.services.gemini_service import GeminiService, has_pending_skill, RateLimitedError, is_valid_model, is_premium_model, resolve_model_for_tier, AI_MODE_NORMAL, AI_MODE_THINKING
from app.services.push_service import PushNotificationService
from app.services import demo_data as _demo
from app.services.wrap_service import log_activity, generate_wrap_for_user

log = logging.getLogger(__name__)

bakalari_bp = Blueprint("bakalari", __name__)

# Free-tier caps (Premium = unlimited). Skill cap lives in gemini_service.
_FREE_MAX_PAGES = 3
_FREE_MAX_CHATS = 3

_COLORS = [
    "#b5451b", "#2d6a4f", "#5c7a9e", "#8b6b3d",
    "#7a4f7a", "#4a7c6b", "#c47d2e", "#5e7a5e",
]

_CZ_MONTHS = [
    "ledna","února","března","dubna","května","června",
    "července","srpna","září","října","listopadu","prosince",
]


def _holiday_info() -> "tuple[bool, int | None, str | None]":
    """Returns (is_holiday, days_until_school, school_start_str).

    Covers Czech summer holidays (Jul 1 – Aug 31) and Christmas (Dec 23 – Jan 1).
    school_start is moved to the next Monday if it falls on a weekend.
    """
    today = datetime.date.today()
    school_start = None

    if today.month in (7, 8):
        school_start = datetime.date(today.year, 9, 1)
    elif today.month == 12 and today.day >= 23:
        school_start = datetime.date(today.year + 1, 1, 2)
    elif today.month == 1 and today.day == 1:
        school_start = datetime.date(today.year, 1, 2)

    if school_start is None:
        return False, None, None

    while school_start.weekday() >= 5:
        school_start += datetime.timedelta(days=1)

    days_left = (school_start - today).days
    start_str = f"{school_start.day}. {_CZ_MONTHS[school_start.month - 1]} {school_start.year}"
    return True, days_left, start_str


def _build_chart_datasets(subjects):
    datasets = []
    for i, subject in enumerate(subjects):
        points = []
        for mark in sorted(subject.get("Marks", []), key=lambda m: m.get("MarkDate", "")):
            try:
                value = int(mark["MarkText"].strip())
                if 1 <= value <= 5:
                    points.append({"x": mark["MarkDate"][:10], "y": value})
            except (ValueError, AttributeError, KeyError):
                pass
        if points:
            color = _COLORS[i % len(_COLORS)]
            datasets.append({
                "label":           subject["Subject"]["Name"],
                "data":            points,
                "borderColor":     color,
                "backgroundColor": color,
            })
    return datasets


_HTML_TAG_RE   = re.compile(r'<[^>]+>')
_HTML_ENTITY_RE = re.compile(r'&(?:nbsp|amp|lt|gt|quot|apos|#\d+|#x[\da-fA-F]+);')

# ── SVG / interactive HTML in chat messages ───────────────────────────────────────────────
_SVG_DETECT_RE = re.compile(r'<(svg|canvas|figure|table)\b', re.I)
_SCRIPT_RE     = re.compile(r'<script\b[^>]*>.*?</script>', re.I | re.S)
_EVENT_ATTR_RE = re.compile(r'\s+on\w+\s*=\s*(?:"[^"]*"|\'[^\']*\')', re.I)
_JS_HREF_RE    = re.compile(r'(href|src)\s*=\s*"javascript:[^"]*"', re.I)


def _prep_chat_msg(text: str) -> "tuple[str, bool]":
    """Return (sanitized_text, is_html).

    is_html=True when the message contains SVG or block-level HTML that should
    be rendered as markup rather than escaped plain text.
    """
    if not _SVG_DETECT_RE.search(text):
        return text, False
    text = _SCRIPT_RE.sub('', text)
    text = _EVENT_ATTR_RE.sub('', text)
    text = _JS_HREF_RE.sub(r'\1="#"', text)
    return text, True

# 30-day TTL so seen-IDs survive across cache expiry cycles
_SEEN_TTL = 2_592_000

_push_svc = PushNotificationService()

_SUB_LABELS = {
    'Cancelled':     'Odpadlo',
    'Substitution':  'Suplování',
    'TeacherChange': 'Náhradník',
    'RoomChange':    'Jiná učebna',
    'Absent':        'Absence',
}


def _notify_substitutions(user_id: str, items: list, date_str: str) -> None:
    """Send push notification for newly-detected substitutions on date_str."""
    prefs = _db_get_settings(user_id)
    if prefs.get("notifications_subs") is False:
        return
    changed = [i for i in items if i.get('status') and i['status'] != 'OK']
    if not changed:
        return
    sub_ids  = {f"{date_str}:{i['hour']}:{i['status']}" for i in changed}
    seen_ids = set(cache_get(user_id, "push_seen_subs", ttl=_SEEN_TTL) or [])
    novel    = [i for i in changed if f"{date_str}:{i['hour']}:{i['status']}" not in seen_ids]
    updated  = seen_ids | sub_ids
    if updated != seen_ids:
        cache_set(user_id, "push_seen_subs", list(updated))
    if not novel:
        return
    first = novel[0]
    count = len(novel)
    label = _SUB_LABELS.get(first['status'], 'Změna')
    when  = "dnes" if date_str == datetime.date.today().isoformat() else "zítra"
    body  = (
        f"{label} {when}: {first['subject']} ({first['time']})"
        if count == 1 else
        f"{count} změn v rozvrhu {when} (první: {first['subject']})"
    )
    _push_svc.send_to_user_async(user_id, "Změna v rozvrhu", body, tag="bakix-subs")


def _fire_push_if_new(user_id: str, seen_key: str, current_ids: set, title: str, body: str) -> None:
    """Push only for IDs not in the persisted seen-set; update the seen-set afterwards.

    Runs the webpush call in a daemon thread so it never blocks the API response.
    """
    seen_ids  = set(cache_get(user_id, seen_key, ttl=_SEEN_TTL) or [])
    novel_ids = current_ids - seen_ids
    if novel_ids:
        _push_svc.send_to_user_async(user_id, title, body)
    updated = seen_ids | current_ids
    if updated != seen_ids:
        cache_set(user_id, seen_key, list(updated))


def _get_svc_and_token():
    user_id = session.get("user_id")
    if not user_id:
        return None, None, None
    row = fetch_row(user_id)
    if not row:
        return None, None, None
    svc = BakalariService(base_url=row["school_url"])
    return svc, svc.get_token(user_id), user_id


@bakalari_bp.route("/api/3/homeworks", methods=["GET"])
@limiter.limit("30 per minute")
def api_homeworks():
    if session.get("is_demo"):
        return jsonify(_demo.DEMO_HOMEWORKS)
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

        today   = datetime.date.today()
        _ck     = f"hw_{today}"

        log_activity(user_id, "homeworks_checked")

        _hit    = cache_get(user_id, _ck)
        if _hit is not None:
            return jsonify(_hit)

        to_date = today + datetime.timedelta(days=7)
        data    = svc.get_homeworks(token, today.isoformat(), to_date.isoformat())

        if data.get("status_code") == 401:
            token = svc.reauth(user_id)
            if not token:
                return jsonify({"error": "Not authenticated"}), 401
            data = svc.get_homeworks(token, today.isoformat(), to_date.isoformat())
            if data.get("status_code") == 401:
                return jsonify({"error": "Not authenticated"}), 401

        if "error" in data:
            return jsonify({"error": f"Nepodařilo se načíst úkoly ({data.get('status_code', '')})"}), 502

        homeworks = sorted(
            [
                {
                    "ID":             hw.get("Id"),
                    "Subject":        (hw.get("Subject") or {}).get("Name"),
                    "Content":        (hw.get("Content") or "")[:100],
                    "DateEnd":        hw.get("DateEnd"),
                    "HasAttachments": bool(hw.get("HasAttachments")),
                }
                for hw in (data.get("Homeworks") if isinstance(data, dict) else []) or []
                if not hw.get("Closed") and not hw.get("Done")
            ],
            key=lambda h: h["DateEnd"] or "",
        )

        hw_ids = {str(h["ID"]) for h in homeworks if h["ID"]}
        if hw_ids:
            seen_ids  = set(cache_get(user_id, "push_seen_hw", ttl=_SEEN_TTL) or [])
            novel_ids = hw_ids - seen_ids
            if novel_ids and _db_get_settings(user_id).get("notifications_homeworks") is not False:
                # Classify topic for the first new homework in a background thread
                first_new = next((h for h in homeworks if str(h["ID"]) in novel_ids), None)
                def _send_hw_push(hw=first_new, count=len(novel_ids)):
                    subject = hw["Subject"] or "" if hw else ""
                    content = hw["Content"] or "" if hw else ""
                    topic   = BakalariService.classify_homework_topic(subject, content)
                    if count == 1 and hw:
                        due  = (hw["DateEnd"] or "")[:10]
                        body = f"{topic} z {subject} – odevzdat do {due}"
                    else:
                        body = f"Máš {count} nových úkolů"
                    _push_svc.send_to_user(user_id, "Nový úkol v Bakixu", body, tag="bakix-hw")
                threading.Thread(target=_send_hw_push, daemon=True).start()
            cache_set(user_id, "push_seen_hw", list(hw_ids | seen_ids))

        cache_set(user_id, _ck, homeworks)
        return jsonify(homeworks)
    except Exception:
        log.exception("api_homeworks: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/3/komens/messages/received", methods=["POST"])
@limiter.limit("30 per minute")
def api_komens():
    if session.get("is_demo"):
        return jsonify(_demo.DEMO_KOMENS)
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

        log_activity(user_id, "komens_checked")

        _hit = cache_get(user_id, "komens")
        if _hit is not None:
            return jsonify(_hit)

        data = svc.get_komens(token)

        if data.get("status_code") == 401:
            token = svc.reauth(user_id)
            if not token:
                return jsonify({"error": "Not authenticated"}), 401
            data = svc.get_komens(token)
            if data.get("status_code") == 401:
                return jsonify({"error": "Not authenticated"}), 401

        if "error" in data:
            return jsonify({"error": f"Nepodařilo se načíst zprávy ({data.get('status_code', '')})"}), 502

        top5 = sorted(
            (data.get("Messages") if isinstance(data, dict) else []) or [],
            key=lambda m: m.get("SentDate") or "",
            reverse=True,
        )[:5]

        def _clean_text(raw: str) -> str:
            text = re.sub(r'<br\s*/?>', '/n', raw, flags=re.IGNORECASE)
            text = _HTML_TAG_RE.sub("", text)
            text = _HTML_ENTITY_RE.sub(" ", text)
            text = re.sub(r'[\r\n]+', '/n', text)
            segs = [' '.join(s.split()) for s in text.split('/n')]
            return '/n'.join(s for s in segs if s)

        result = [
            {
                "Id":       m.get("Id"),
                "Title":    m.get("Title"),
                "Sender":   (m.get("Sender") or {}).get("Name"),
                "SentDate": m.get("SentDate"),
                "Read":     bool(m.get("Read")),
                "Text":     _clean_text(m.get("Text") or ""),
            }
            for m in top5
        ]

        msg_ids = {str(m["Id"]) for m in result if m["Id"]}
        if msg_ids:
            seen_ids     = set(cache_get(user_id, "push_seen_komens", ttl=_SEEN_TTL) or [])
            novel_unread = [m for m in result if str(m["Id"]) not in seen_ids and not m["Read"]]
            if novel_unread:
                first        = novel_unread[0]
                sender       = first["Sender"] or "škola"
                title_t      = (first["Title"] or "Zpráva")[:60]
                text_preview = (first["Text"] or "")[:80]
                notif_body   = f"{sender}: {text_preview}" if text_preview else f"{sender}: {title_t}"
                if _db_get_settings(user_id).get("notifications_messages") is not False:
                    _push_svc.send_to_user_async(user_id, "Nová zpráva v Bakixu", notif_body, tag="bakix-komens")
            updated = seen_ids | msg_ids
            if updated != seen_ids:
                cache_set(user_id, "push_seen_komens", list(updated))

        cache_set(user_id, "komens", result)
        return jsonify(result)
    except Exception:
        log.exception("api_komens: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/komens/message-types", methods=["GET"])
@limiter.limit("30 per minute")
def api_komens_message_types():
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401
        data = svc.get_message_types(token)
        if data.get("status_code") == 401:
            token = svc.reauth(user_id)
            if not token:
                return jsonify({"error": "Not authenticated"}), 401
            data = svc.get_message_types(token)
            if data.get("status_code") == 401:
                return jsonify({"error": "Not authenticated"}), 401
        if "error" in data:
            return jsonify({"error": f"Nepodařilo se načíst příjemce ({data.get('status_code', '')})"}), 502
        recipients = [
            {"code": r.get("Code"), "name": r.get("Name") or r.get("DisplayName")}
            for r in (data.get("Recipients") or [])
            if r.get("Code")
        ]
        return jsonify({"recipients": recipients})
    except Exception:
        log.exception("api_komens_message_types: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/komens/send", methods=["POST"])
@limiter.limit("5 per minute")
def api_komens_send():
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401
        body         = request.get_json(force=True, silent=True) or {}
        recipient_id = (body.get("recipient_id") or "").strip()
        subject      = (body.get("subject") or "").strip()
        content      = (body.get("content") or "").strip()
        if not recipient_id or not subject or not content:
            return jsonify({"error": "Chybí příjemce, předmět nebo text."}), 400
        result = svc.send_komens_message(token, recipient_id, subject, content)
        if result.get("status_code") == 401:
            token = svc.reauth(user_id)
            if not token:
                return jsonify({"error": "Not authenticated"}), 401
            result = svc.send_komens_message(token, recipient_id, subject, content)
            if result.get("status_code") == 401:
                return jsonify({"error": "Not authenticated"}), 401
        if "error" in result:
            return jsonify({"error": result.get("error", "Odeslání selhalo.")}), 502
        return jsonify({"ok": True})
    except Exception:
        log.exception("api_komens_send: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/3/absence/student", methods=["GET"])
@limiter.limit("30 per minute")
def api_absences():
    if session.get("is_demo"):
        return jsonify(_demo.DEMO_ABSENCES)
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

        _hit = cache_get(user_id, "absences", ttl=300)
        if _hit is not None:
            return jsonify(_hit)

        data = svc.get_absences(token)

        if data.get("status_code") == 401:
            token = svc.reauth(user_id)
            if not token:
                return jsonify({"error": "Not authenticated"}), 401
            data = svc.get_absences(token)
            if data.get("status_code") == 401:
                return jsonify({"error": "Not authenticated"}), 401

        if "error" in data:
            return jsonify({"error": f"Nepodařilo se načíst absence ({data.get('status_code', '')})"}), 502

        cache_set(user_id, "absences", data)
        return jsonify(data)
    except Exception:
        log.exception("api_absences: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/3/marks", methods=["GET"])
@limiter.limit("30 per minute")
def api_marks():
    if session.get("is_demo"):
        return jsonify(_demo.DEMO_MARKS_API)
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

        log_activity(user_id, "marks_checked")

        _hit = cache_get(user_id, "marks")
        if _hit is not None:
            return jsonify(_hit)

        data = svc.get_marks(token)

        if data.get("status_code") == 401:
            token = svc.reauth(user_id)
            if not token:
                return jsonify({"error": "Not authenticated"}), 401
            data = svc.get_marks(token)
            if data.get("status_code") == 401:
                return jsonify({"error": "Not authenticated"}), 401

        if "error" in data:
            return jsonify({"error": f"Nepodařilo se načíst známky ({data.get('status_code', '')})"}), 502

        subjects = [
            {
                "Subject": {
                    "Name":   (s.get("Subject") or {}).get("Name"),
                    "Abbrev": (s.get("Subject") or {}).get("Abbrev"),
                },
                "AverageText": s.get("AverageText"),
                "Marks": [
                    {
                        "MarkText": m.get("MarkText"),
                        "Weight":   m.get("Weight"),
                        "Caption":  m.get("Caption"),
                        "IsPoints": bool(m.get("IsPoints")),
                        "EditDate": m.get("EditDate"),
                    }
                    for m in (s.get("Marks") or [])
                ],
            }
            for s in (data.get("Subjects") if isinstance(data, dict) else []) or []
        ]

        mark_ids = {
            f"{s['Subject']['Name']}:{m['MarkText']}:{m['EditDate']}"
            for s in subjects
            for m in s["Marks"]
            if m.get("EditDate")
        }
        if mark_ids:
            seen_ids  = set(cache_get(user_id, "push_seen_marks", ttl=_SEEN_TTL) or [])
            novel_ids = mark_ids - seen_ids
            if novel_ids:
                first_id = next(iter(novel_ids))
                parts    = first_id.split(":", 2)
                subj_nm  = parts[0] if len(parts) > 0 else "předmět"
                mark_txt = parts[1] if len(parts) > 1 else "?"
                count    = len(novel_ids)
                body = (
                    f"{mark_txt} z {subj_nm}" if count == 1
                    else f"{count} nových známek (první: {mark_txt} z {subj_nm})"
                )
                if _db_get_settings(user_id).get("notifications_marks") is not False:
                    _push_svc.send_to_user_async(user_id, "Nová známka v Bakixu", body, tag="bakix-marks")
                cache_set(user_id, "push_seen_marks", list(seen_ids | mark_ids))

        cache_set(user_id, "marks", subjects)
        return jsonify(subjects)
    except Exception:
        log.exception("api_marks: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/3/subjects/themes/<string:subject_id>", methods=["GET"])
@limiter.limit("30 per minute")
def api_subject_themes(subject_id):
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

        from app.database.db import cache_get, cache_set
        cache_key = f"themes_{subject_id}"
        cached = cache_get(user_id, cache_key, ttl=604_800)
        if cached is not None:
            return jsonify({"themes": cached})

        data = svc.get_subject_themes(token, subject_id)
        if data.get("status_code") == 401:
            token = svc.reauth(user_id)
            if not token:
                return jsonify({"error": "Not authenticated"}), 401
            data = svc.get_subject_themes(token, subject_id)
            if data.get("status_code") == 401:
                return jsonify({"error": "Not authenticated"}), 401

        if "error" in data:
            return jsonify({"error": f"Nepodařilo se načíst témata ({data.get('status_code', '')})"}), 502

        themes_raw = data.get("Themes") or data.get("themes") or []
        themes = []
        for t in themes_raw:
            if not isinstance(t, dict):
                continue
            name = t.get("Title") or t.get("Name") or t.get("name") or ""
            date = (t.get("Date") or "")[:10]
            if name:
                themes.append({"name": name, "date": date})

        cache_set(user_id, cache_key, themes)
        return jsonify({"themes": themes})
    except Exception:
        log.exception("api_subject_themes: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/3/timetable/actual", methods=["GET"])
@limiter.limit("30 per minute")
def get_today_timetable():
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

        data = svc.get_timetable(token)

        if data.get("status_code") == 401:
            token = svc.reauth(user_id)
            if not token:
                return jsonify({"error": "Not authenticated"}), 401
            data = svc.get_timetable(token)
            if data.get("status_code") == 401:
                return jsonify({"error": "Not authenticated"}), 401

        if "error" in data:
            return jsonify({"error": f"Nepodařilo se načíst rozvrh ({data.get('status_code', '')})"}), 502

        return jsonify(data)
    except Exception:
        log.exception("get_today_timetable: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/dashboard/today", methods=["GET"])
@limiter.limit("30 per minute")
def api_dashboard_today():
    if session.get("is_demo"):
        return jsonify(_demo.DEMO_TIMETABLE_TODAY)
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

        _hit = cache_get(user_id, "tt_today")
        if _hit is not None:
            return jsonify(_hit)

        data = svc.get_timetable(token)

        if data.get("status_code") == 401:
            token = svc.reauth(user_id)
            if not token:
                return jsonify({"error": "Not authenticated"}), 401
            data = svc.get_timetable(token)
            if data.get("status_code") == 401:
                return jsonify({"error": "Not authenticated"}), 401

        if "error" in data:
            return jsonify({"error": f"Nepodařilo se načíst rozvrh ({data.get('status_code', '')})"}), 502

        if not isinstance(data, dict):
            log.error("api_dashboard_today: unexpected timetable shape: %s", type(data))
            return jsonify({"error": "Neočekávaná odpověď serveru"}), 502

        # ── Build O(1) lookup dicts from the top-level helper arrays ────────────────────
        subjects = {s["Id"]: s["Name"]   for s in (data.get("Subjects") or []) if s.get("Id")}
        teachers = {t["Id"]: t["Name"]   for t in (data.get("Teachers") or []) if t.get("Id")}
        rooms    = {r["Id"]: r["Abbrev"] for r in (data.get("Rooms")    or []) if r.get("Id")}
        hours    = {
            h["Id"]: f"{h['BeginTime'][:5]}-{h['EndTime'][:5]}"
            for h in (data.get("Hours") or [])
            if h.get("Id") and h.get("BeginTime") and h.get("EndTime")
        }

        # ── Locate today's day block ──────────────────────────────────────────────────────
        today = datetime.date.today().isoformat()
        today_day = next(
            (d for d in (data.get("Days") or []) if (d.get("Date") or "").startswith(today)),
            None,
        )
        if not today_day:
            return jsonify([])

        # ── Map atoms to output records ──────────────────────────────────────────────────────
        result = []
        for atom in (today_day.get("Atoms") or []):
            change      = atom.get("Change")     # dict or None
            hour_id     = atom.get("HourId")
            change_type = (change or {}).get("ChangeType") or None
            description = (change or {}).get("Description") or None
            result.append({
                "hour":        hour_id,
                "subject":     subjects.get(atom.get("SubjectId"), "—"),
                "teacher":     teachers.get(atom.get("TeacherId"), "—"),
                "time":        hours.get(hour_id, "—"),
                "room":        rooms.get(atom.get("RoomId"), "—"),
                "status":      change_type or "OK",
                "change_info": description,
            })

        result.sort(key=lambda x: x["hour"] or 0)
        _notify_substitutions(user_id, result, today)
        cache_set(user_id, "tt_today", result)
        return jsonify(result)
    except Exception:
        log.exception("api_dashboard_today: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/dashboard/tomorrow", methods=["GET"])
@limiter.limit("30 per minute")
def api_dashboard_tomorrow():
    if session.get("is_demo"):
        return jsonify(_demo.DEMO_TIMETABLE_TOMORROW)
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

        _hit = cache_get(user_id, "tt_tomorrow")
        if _hit is not None:
            return jsonify(_hit)

        data = svc.get_timetable(token)

        if data.get("status_code") == 401:
            token = svc.reauth(user_id)
            if not token:
                return jsonify({"error": "Not authenticated"}), 401
            data = svc.get_timetable(token)
            if data.get("status_code") == 401:
                return jsonify({"error": "Not authenticated"}), 401

        if "error" in data:
            return jsonify({"error": f"Nepodařilo se načíst rozvrh ({data.get('status_code', '')})"}), 502

        if not isinstance(data, dict):
            return jsonify({"error": "Neočekávaná odpověď serveru"}), 502

        subjects = {s["Id"]: s["Name"]   for s in (data.get("Subjects") or []) if s.get("Id")}
        teachers = {t["Id"]: t["Name"]   for t in (data.get("Teachers") or []) if t.get("Id")}
        rooms    = {r["Id"]: r["Abbrev"] for r in (data.get("Rooms")    or []) if r.get("Id")}
        hours    = {
            h["Id"]: f"{h['BeginTime'][:5]}-{h['EndTime'][:5]}"
            for h in (data.get("Hours") or [])
            if h.get("Id") and h.get("BeginTime") and h.get("EndTime")
        }

        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        tomorrow_day = next(
            (d for d in (data.get("Days") or []) if (d.get("Date") or "").startswith(tomorrow)),
            None,
        )
        if not tomorrow_day:
            return jsonify([])

        result = []
        for atom in (tomorrow_day.get("Atoms") or []):
            change      = atom.get("Change")
            hour_id     = atom.get("HourId")
            result.append({
                "hour":        hour_id,
                "subject":     subjects.get(atom.get("SubjectId"), "—"),
                "teacher":     teachers.get(atom.get("TeacherId"), "—"),
                "time":        hours.get(hour_id, "—"),
                "room":        rooms.get(atom.get("RoomId"), "—"),
                "status":      (change or {}).get("ChangeType") or "OK",
                "change_info": (change or {}).get("Description") or None,
            })

        result.sort(key=lambda x: x["hour"] or 0)
        _notify_substitutions(user_id, result, tomorrow)
        cache_set(user_id, "tt_tomorrow", result)
        return jsonify(result)
    except Exception:
        log.exception("api_dashboard_tomorrow: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


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
            return jsonify({"error": result.get("error", "Přihlášení selhalo.")}), 401
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


@bakalari_bp.route("/api/gemini/insights", methods=["GET"])
@limiter.limit("10 per minute")
def api_gemini_insights():
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

        marks_data = svc.get_marks(token)
        if marks_data.get("status_code") == 401:
            token = svc.reauth(user_id)
            if not token:
                return jsonify({"error": "Not authenticated"}), 401
            marks_data = svc.get_marks(token)
            if marks_data.get("status_code") == 401:
                return jsonify({"error": "Not authenticated"}), 401

        today   = datetime.date.today()
        to_date = today + datetime.timedelta(days=7)
        hw_data = svc.get_homeworks(token, today.isoformat(), to_date.isoformat())
        if hw_data.get("status_code") == 401:
            hw_data = {}

        subjects_summary = [
            {
                "subject": (s.get("Subject") or {}).get("Name"),
                "average": s.get("AverageText"),
                "marks":   [m.get("MarkText") for m in (s.get("Marks") or [])],
            }
            for s in (marks_data.get("Subjects") if isinstance(marks_data, dict) else []) or []
        ]
        homeworks_summary = [
            {
                "subject": (hw.get("Subject") or {}).get("Name"),
                "content": (hw.get("Content") or "")[:80],
                "due":     hw.get("DateEnd"),
            }
            for hw in (hw_data.get("Homeworks") if isinstance(hw_data, dict) else []) or []
            if not hw.get("Closed") and not hw.get("Done")
        ]

        payload = {"marks": subjects_summary, "upcoming_homeworks": homeworks_summary}

        try:
            gemini = GeminiService()
            result = gemini.get_proactive_insights(payload)
        except ValueError:
            return jsonify({"error": "GEMINI_API_KEY not configured"}), 503

        return jsonify(result)
    except Exception:
        log.exception("api_gemini_insights: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/gemini/chat", methods=["POST"])
@limiter.limit("20 per minute")
def api_gemini_chat():
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Not authenticated"}), 401

        body    = request.get_json(force=True, silent=True) or {}
        message = (body.get("message") or "").strip()
        history = body.get("history") or []

        if not message:
            return jsonify({"error": "Prázdná zpráva"}), 400

        try:
            gemini = GeminiService()
            reply  = gemini.send_chat_message(history, message)
        except ValueError:
            return jsonify({"error": "GEMINI_API_KEY not configured"}), 503

        return jsonify({"reply": reply})
    except Exception:
        log.exception("api_gemini_chat: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


# ── Generated-page helpers ────────────────────────────────────────────────────────────────

def get_user_projects(user_id: str) -> list:
    """Return [{page_id, topic}] for all generated pages owned by user_id."""
    from app.database.db import list_generated_pages
    return list_generated_pages(user_id)


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


@bakalari_bp.route("/api/ai/pages", methods=["GET"])
@limiter.limit("30 per minute")
def api_ai_pages():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    return jsonify(get_user_projects(user_id))


# ── Conversations (multiple chats per user) ─────────────────────────────────

def _resolve_conversation(user_id: str, raw_id) -> str:
    """Return a valid conversation id owned by user_id, creating one if needed."""
    from app.database.db import get_conversation, create_conversation
    raw_id = (raw_id or "").strip()
    if raw_id and get_conversation(raw_id, user_id):
        return raw_id
    return create_conversation(user_id)


@bakalari_bp.route("/api/ai/conversations", methods=["GET"])
@limiter.limit("30 per minute")
def api_conversations_list():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    from app.database.db import list_conversations
    return jsonify(list_conversations(user_id))


@bakalari_bp.route("/api/ai/conversations", methods=["POST"])
@limiter.limit("20 per minute")
def api_conversations_create():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    from app.database.db import create_conversation, count_conversations, get_subscription_tier
    if get_subscription_tier(user_id) != "premium" and count_conversations(user_id) >= _FREE_MAX_CHATS:
        return jsonify({
            "error": "chat_limit",
            "message": f"Ve free verzi můžeš mít {_FREE_MAX_CHATS} chaty. Smaž některý, nebo přejdi na Premium pro neomezené chaty. ✦",
            "tier": "free",
        }), 403
    body  = request.get_json(force=True, silent=True) or {}
    title = (body.get("title") or "Nový chat").strip()[:80] or "Nový chat"
    conv_id = create_conversation(user_id, title)
    return jsonify({"id": conv_id, "title": title})


@bakalari_bp.route("/api/ai/conversations/<conversation_id>", methods=["PATCH"])
@limiter.limit("30 per minute")
def api_conversations_rename(conversation_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    from app.database.db import rename_conversation
    body  = request.get_json(force=True, silent=True) or {}
    title = (body.get("title") or "").strip()[:80]
    if not title:
        return jsonify({"error": "Chybí název"}), 400
    if not rename_conversation(conversation_id, user_id, title):
        return abort(404)
    return jsonify({"ok": True, "title": title})


@bakalari_bp.route("/api/ai/conversations/<conversation_id>", methods=["DELETE"])
@limiter.limit("30 per minute")
def api_conversations_delete(conversation_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    from app.database.db import delete_conversation
    if not delete_conversation(conversation_id, user_id):
        return abort(404)
    return jsonify({"ok": True})


@bakalari_bp.route("/api/ai/conversations/<conversation_id>/messages", methods=["GET"])
@limiter.limit("30 per minute")
def api_conversations_messages(conversation_id):
    """Return the rendered messages of one conversation for rehydrating the thread."""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    from app.database.db import get_conversation_history_rows
    rows = get_conversation_history_rows(conversation_id, user_id)
    if rows is None:
        return abort(404)

    out = []
    for r in rows:
        role = r["role"]
        if role == "user":
            out.append({"role": "user", "message": r["content"], "is_html": False,
                        "timestamp": r["timestamp"]})
            continue
        # model rows store the full ai_result JSON (or a "[page modified: …]" note)
        text = r["content"]
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and "message" in parsed:
                text = parsed.get("message") or ""
        except (ValueError, TypeError):
            pass
        if not text:
            continue
        msg, is_html = _prep_chat_msg(text)
        out.append({"role": "model", "message": msg, "is_html": is_html,
                    "timestamp": r["timestamp"]})
    return jsonify({"id": conversation_id, "messages": out})


# ── AI chat endpoint (structured response with optional page generation) ─────

@bakalari_bp.route("/api/ai/chat", methods=["POST"])
@limiter.limit("30 per minute")
def api_ai_chat():
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Not authenticated"}), 401

        body      = request.get_json(force=True, silent=True) or {}
        message   = (body.get("message") or "").strip()
        chat_mode = (body.get("chat_mode") or "auto").strip().lower()
        _raw_model = (body.get("model_id") or "").strip()
        model_id  = _raw_model if is_valid_model(_raw_model) else None
        ai_mode   = AI_MODE_THINKING if (body.get("ai_mode") or "") == AI_MODE_THINKING else AI_MODE_NORMAL

        if not message:
            return jsonify({"error": "Prázdná zpráva"}), 400

        # ── Premium gating ────────────────────────────────────────────────────
        from app.database.db import (
            get_subscription_tier, get_conversation, create_conversation,
            count_conversations, count_generated_pages, set_conversation_title_if_default,
        )
        tier = get_subscription_tier(user_id)
        # #1 Thinking mode is Premium-only — silently fall back to Normal for free.
        thinking_locked = False
        if ai_mode == AI_MODE_THINKING and tier != "premium":
            ai_mode = AI_MODE_NORMAL
            thinking_locked = True
        # #2 Pro models are Premium-only — free users get the freemium default.
        model_id, model_locked = resolve_model_for_tier(model_id, tier)

        # ── Resolve / create the conversation (with the free chat cap, #4) ────
        conversation_id = (body.get("conversation_id") or "").strip()
        if conversation_id and not get_conversation(conversation_id, user_id):
            conversation_id = ""
        if not conversation_id:
            if tier != "premium" and count_conversations(user_id) >= _FREE_MAX_CHATS:
                return jsonify({
                    "error": "chat_limit",
                    "message": f"Ve free verzi můžeš mít {_FREE_MAX_CHATS} chaty. "
                               "Smaž některý v 🗂, nebo přejdi na Premium pro neomezené chaty. ✦",
                    "tier": "free",
                }), 403
            conversation_id = create_conversation(user_id)
        # Name a still-unnamed chat after its first message.
        set_conversation_title_if_default(conversation_id, message)

        _msg_lower = message.lower().strip()

        # /studie plan — personalised study plan
        if _msg_lower in ("/studie plan", "/studie plán", "/studijní plán", "/studijni plan"):
            try:
                from app.services.weekly_summary import generate_study_plan_for_user
                _result = generate_study_plan_for_user(user_id)
            except Exception:
                log.exception("api_ai_chat: study plan command failed")
                _result = None

            if _result is None:
                _plan_msg = "Studijní plán se nepodařilo vygenerovat. Zkontroluj připojení k Bakalářům."
            else:
                _parts = [_result.get("plan", "")]
                if _result.get("priority_tasks"):
                    _parts.append("**Prioritní úkoly:**\n" + "\n".join(f"• {t}" for t in _result["priority_tasks"]))
                if _result.get("study_slots"):
                    _parts.append(f"**Studijní okna:** {_result['study_slots']}")
                if _result.get("tip"):
                    _parts.append(f"**Tip:** {_result['tip']}")
                _plan_msg = "\n\n".join(p for p in _parts if p)

            _plan_msg, _plan_html = _prep_chat_msg(_plan_msg)
            return jsonify({
                "conversation_id": conversation_id,
                "message":      _plan_msg,
                "is_html":      _plan_html,
                "action_url":   None,
                "action_label": None,
                "is_test":      False,
                "sender":       "ai",
                "timestamp":    datetime.datetime.utcnow().isoformat() + "Z",
            })

        # "Vysvětlit přes AI:" — contextual text explanation from chat selection
        if _msg_lower.startswith("vysvětlit přes ai:"):
            _explain_term = message[len("Vysvětlit přes AI:"):].strip()
            if _explain_term:
                try:
                    ai_result = GeminiService().explain_term(user_id, conversation_id, _explain_term, model_id=model_id, ai_mode=ai_mode)
                except ValueError:
                    return jsonify({"error": "GEMINI_API_KEY not configured"}), 503
                _exp_msg, _exp_html = _prep_chat_msg(ai_result.get("message", ""))
                return jsonify({
                    "conversation_id": conversation_id,
                    "message":      _exp_msg,
                    "is_html":      _exp_html,
                    "action_url":   None,
                    "action_label": None,
                    "is_test":      False,
                    "sender":       "ai",
                    "timestamp":    datetime.datetime.utcnow().isoformat() + "Z",
                })

        # /shrnutí commands — short-circuit to period summaries
        if _msg_lower in ("/shrnutí den", "/shrnuti den", "/shrnutí", "/shrnuti"):
            _is_daily = "den" in _msg_lower
            try:
                if _is_daily:
                    from app.services.weekly_summary import generate_daily_summary_for_user
                    _result = generate_daily_summary_for_user(user_id)
                else:
                    from app.services.weekly_summary import generate_weekly_summary_for_user
                    _result = generate_weekly_summary_for_user(user_id)
            except Exception:
                log.exception("api_ai_chat: summary command failed")
                _result = None

            if _result is None:
                _summary_msg = "Shrnutí se nepodařilo vygenerovat. Zkontroluj připojení k Bakalářům."
            else:
                _parts = [_result.get("summary", "")]
                if _result.get("weak_subjects"):
                    _parts.append("**Slabá místa:** " + ", ".join(_result["weak_subjects"]))
                if _result.get("study_plan"):
                    _label = "Tip na dnešní večer" if _is_daily else "Plán na příští týden"
                    _parts.append(f"**{_label}:** {_result['study_plan']}")
                if _result.get("cta"):
                    _parts.append(_result["cta"])
                _summary_msg = "\n\n".join(p for p in _parts if p)

            _summary_msg, _summary_html = _prep_chat_msg(_summary_msg)
            return jsonify({
                "conversation_id": conversation_id,
                "message":      _summary_msg,
                "is_html":      _summary_html,
                "action_url":   None,
                "action_label": None,
                "is_test":      False,
                "sender":       "ai",
                "timestamp":    datetime.datetime.utcnow().isoformat() + "Z",
            })

        # /skill command or active skill-creation questionnaire — short-circuit normal flow
        if message.startswith("/skill") or has_pending_skill(user_id):
            try:
                ai_result = GeminiService().handle_skill_command(user_id, message)
            except ValueError:
                return jsonify({"error": "GEMINI_API_KEY not configured"}), 503
            _skill_msg, _skill_html = _prep_chat_msg(ai_result.get("message", ""))
            return jsonify({
                "conversation_id": conversation_id,
                "message":      _skill_msg,
                "is_html":      _skill_html,
                "action_url":   None,
                "action_label": None,
                "is_test":      False,
                "sender":       "ai",
                "timestamp":    datetime.datetime.utcnow().isoformat() + "Z",
            })

        # Best-effort: fetch student marks for grade-context routing.
        # Skipped when chat_mode == "general" to avoid unnecessary API calls.
        student_data = None
        flat_grades: list = []
        if chat_mode != "general":
            try:
                svc, token, _ = _get_svc_and_token()
                if token:
                    marks_raw = svc.get_marks(token)
                    if isinstance(marks_raw, dict) and "Subjects" in marks_raw:
                        student_data = {
                            "subjects": [
                                {
                                    "name":    (s.get("Subject") or {}).get("Name"),
                                    "average": s.get("AverageText"),
                                }
                                for s in marks_raw["Subjects"] or []
                            ]
                        }
                        for s in marks_raw["Subjects"] or []:
                            subject_name = (s.get("Subject") or {}).get("Name", "")
                            for m in s.get("Marks") or []:
                                flat_grades.append({
                                    "subject":   subject_name,
                                    "MarkText":  m.get("MarkText"),
                                    "Caption":   m.get("Caption") or "",
                                    "EditDate":  m.get("EditDate") or "",
                                    "timestamp": m.get("EditDate") or "",
                                    "topic":     m.get("Caption") or "",
                                })
            except Exception:
                pass

        try:
            gemini = GeminiService()
            if chat_mode == "grades" or (chat_mode == "auto" and flat_grades):
                ai_result = gemini.handle_grades_context(user_id, conversation_id, flat_grades, message, model_id=model_id, ai_mode=ai_mode)
            else:
                ai_result = gemini.get_response(user_id, conversation_id, message, student_data, model_id=model_id, ai_mode=ai_mode)
        except ValueError:
            return jsonify({"error": "GEMINI_API_KEY not configured"}), 503

        action_url = None
        page_limit_reached = False
        html_body  = ai_result.get("page_content_html") or ""
        if ai_result.get("intent") == "create_page" and html_body.strip():
            # #3 Free users keep at most _FREE_MAX_PAGES saved study pages.
            if tier != "premium" and count_generated_pages(user_id) >= _FREE_MAX_PAGES:
                page_limit_reached = True
            else:
                from app.database.db import create_generated_page
                page_id = uuid.uuid4().hex
                create_generated_page(
                    page_id, user_id, ai_result.get("page_title") or "AI obsah", html_body,
                )
                action_url = f"/api/ai/generated/{page_id}"

        chat_msg, is_html = _prep_chat_msg(ai_result.get("message", ""))
        if page_limit_reached:
            chat_msg += (
                f"\n\n_(Dosáhl jsi limitu {_FREE_MAX_PAGES} uložených stránek ve free verzi. "
                "Smaž některou přes ✦, nebo přejdi na Premium pro neomezené stránky.)_"
            )
        resp = {
            "conversation_id": conversation_id,
            "message":      chat_msg,
            "is_html":      is_html,
            "action_url":   action_url,
            "action_label": ai_result.get("action_label") if action_url else None,
            "is_test":      bool(ai_result.get("is_test", False)),
            "sender":       "ai",
            "timestamp":    datetime.datetime.utcnow().isoformat() + "Z",
        }
        if thinking_locked:    resp["thinking_locked"] = True
        if model_locked:       resp["model_locked"] = True
        if page_limit_reached: resp["page_limit_reached"] = True
        if ai_result.get("rate_limited"):
            resp["rate_limited"] = True
            resp["tier"]         = ai_result.get("tier", "free")
        return jsonify(resp)
    except Exception:
        log.exception("api_ai_chat: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/ai/generated/<page_id>", methods=["GET"])
@limiter.limit("30 per minute")
def api_ai_generated(page_id):
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("welcome"))

    # Strict validation: uuid4().hex is 32 lowercase hex chars
    if not page_id.isalnum() or len(page_id) > 32:
        return abort(404)

    from app.database.db import get_generated_page
    page = get_generated_page(page_id)
    if not page or page["user_id"] != user_id:
        return abort(404)

    return render_template(
        "generated_page.html",
        title=page["title"] or "AI obsah",
        content=Markup(_sanitize_html(page["html"])),
        page_id=page_id,
    )


@bakalari_bp.route("/api/ai/generated/<page_id>", methods=["PUT"])
@limiter.limit("30 per minute")
def api_ai_generated_update(page_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    if not page_id.isalnum() or len(page_id) > 32:
        return abort(404)

    body    = request.get_json(force=True, silent=True) or {}
    content = (body.get("content") or "").strip()
    if not content:
        return jsonify({"error": "Prázdný obsah"}), 400

    from app.database.db import update_generated_page_html
    if not update_generated_page_html(page_id, user_id, content):
        return abort(404)
    return jsonify({"ok": True})


@bakalari_bp.route("/api/ai/generated/<page_id>", methods=["DELETE"])
@limiter.limit("30 per minute")
def api_ai_generated_delete(page_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    if not page_id.isalnum() or len(page_id) > 32:
        return abort(404)

    from app.database.db import delete_generated_page
    if not delete_generated_page(page_id, user_id):
        return abort(404)
    return jsonify({"ok": True})


@bakalari_bp.route("/api/ai/regen/<page_id>", methods=["POST"])
@limiter.limit("20 per minute")
def api_ai_regen(page_id):
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Not authenticated"}), 401

        if not page_id.isalnum() or len(page_id) > 32:
            return abort(404)

        from app.database.db import get_generated_page, update_generated_page_html
        page = get_generated_page(page_id)
        if not page or page["user_id"] != user_id:
            return abort(404)
        current_html = page["html"]

        body   = request.get_json(force=True, silent=True) or {}
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            return jsonify({"error": "Prázdný požadavek"}), 400

        student_data = None
        try:
            svc, token, _ = _get_svc_and_token()
            if token:
                marks_raw = svc.get_marks(token)
                if isinstance(marks_raw, dict) and "Subjects" in marks_raw:
                    student_data = {
                        "subjects": [
                            {"name": (s.get("Subject") or {}).get("Name"), "average": s.get("AverageText")}
                            for s in marks_raw["Subjects"] or []
                        ]
                    }
        except Exception:
            pass

        try:
            new_html = GeminiService().regenerate_page(current_html, prompt, student_data, user_id=user_id)
        except RateLimitedError as exc:
            return jsonify({"ok": False, "error": "rate_limited", "tier": exc.tier}), 429
        except ValueError:
            return jsonify({"error": "GEMINI_API_KEY not configured"}), 503

        update_generated_page_html(page_id, user_id, new_html)
        return jsonify({"ok": True})
    except Exception:
        log.exception("api_ai_regen: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/ai/modify/<page_id>", methods=["POST"])
@limiter.limit("20 per minute")
def api_ai_modify(page_id):
    """Stateful page modification — uses persistent conversation history per user.

    Accepts {"prompt": "..."} and rewrites the stored page HTML using Gemini
    with the full conversation context, so accumulated instructions (e.g.
    'modern design', 'focus on Matematika') carry forward across requests.
    """
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Not authenticated"}), 401

        if not page_id.isalnum() or len(page_id) > 32:
            return abort(404)

        from app.database.db import get_generated_page, update_generated_page_html
        page = get_generated_page(page_id)
        if not page or page["user_id"] != user_id:
            return abort(404)

        body   = request.get_json(force=True, silent=True) or {}
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            return jsonify({"error": "Prázdný požadavek"}), 400

        current_html = page["html"]
        conversation_id = _resolve_conversation(user_id, body.get("conversation_id"))

        try:
            new_html = GeminiService().modify_page(user_id, conversation_id, current_html, prompt)
        except RateLimitedError as exc:
            return jsonify({"ok": False, "error": "rate_limited", "tier": exc.tier}), 429
        except ValueError:
            return jsonify({"error": "GEMINI_API_KEY not configured"}), 503

        update_generated_page_html(page_id, user_id, new_html)

        return jsonify({"ok": True, "page_url": f"/api/ai/generated/{page_id}"})
    except Exception:
        log.exception("api_ai_modify: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/wrap")
def wrap_page():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("welcome"))
    return render_template("wrap.html")


@bakalari_bp.route("/api/wrap/data")
@limiter.limit("10 per minute")
def api_wrap_data():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    try:
        stats = generate_wrap_for_user(user_id)
        return jsonify(stats)
    except Exception:
        log.exception("api_wrap_data: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/shrnutí", methods=["GET", "POST"])
def api_shrnuti():
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Not authenticated"}), 401

        from app.services.weekly_summary import generate_weekly_summary_for_user
        result = generate_weekly_summary_for_user(user_id)
        if result is None:
            return jsonify({"error": "Shrnutí se nepodařilo vygenerovat"}), 503

        return jsonify(result)
    except Exception:
        log.exception("api_shrnuti: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/")
def index():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("welcome"))

    if session.get("is_demo"):
        chart_datasets = _build_chart_datasets(_demo.DEMO_SUBJECTS_RAW)
        _is_holiday_d, _days_d, _start_d = _holiday_info()
        return render_template(
            "index.html",
            error=None,
            subjects=_demo.DEMO_SUBJECTS_RAW,
            marks_error=None,
            substitutions=None,
            subs_error=None,
            chart_data_json=json.dumps(chart_datasets, ensure_ascii=False),
            user_projects=[],
            display_name="Demo uživatel",
            is_premium=False,
            show_wrap=(os.getenv("DEBUG") == "True") or (datetime.date.today().month in (6, 12)),
            is_holiday=_is_holiday_d,
            days_until_school=_days_d,
            school_start_date=_start_d,
        )

    row = fetch_row(user_id)
    if not row:
        log.warning("index: no DB row for user=%.8s, clearing session", user_id)
        session.clear()
        return redirect(url_for("welcome"))

    svc   = BakalariService(base_url=row["school_url"])
    token = svc.get_token(user_id)
    if not token:
        # Credentials couldn't be refreshed — ask the user to log in again
        # without destroying the session cookie (preserves user_id for UX).
        log.warning("index: get_token returned None for user=%.8s, redirecting to login", user_id)
        return redirect(url_for("login.login"))

    marks_data = svc.get_marks(token)

    if marks_data.get("status_code") == 401:
        # Token expired — attempt re-authentication from stored credentials
        log.info("index: token expired for user=%.8s, reauthenticating", user_id)
        token = svc.reauth(user_id)
        if not token:
            log.warning("index: reauth failed for user=%.8s, redirecting to login", user_id)
            return redirect(url_for("login.login"))
        marks_data = svc.get_marks(token)
        if marks_data.get("status_code") == 401:
            # Password likely changed in Bakaláři — need fresh credentials
            log.warning("index: reauth still returned 401 for user=%.8s, redirecting to login", user_id)
            session.clear()
            return redirect(url_for("login.login"))

    if "error" in marks_data:
        subjects    = None
        marks_error = f"Nepodařilo se načíst známky ({marks_data['status_code']})"
    else:
        subjects    = marks_data.get("Subjects", [])
        marks_error = None

    subs_raw = svc.get_substitutions_from_timetable(token)
    if isinstance(subs_raw, dict) and "error" in subs_raw:
        substitutions = None
        subs_error    = f"Nepodařilo se načíst suplování ({subs_raw['status_code']})"
    else:
        substitutions = subs_raw
        subs_error    = None

    chart_datasets = _build_chart_datasets(subjects) if subjects else []

    display_name = (row.get("display_name") or "") if row else ""

    _show_wrap = (os.getenv("DEBUG") == "True") or (datetime.date.today().month in (6, 12))

    from app.database.db import get_subscription_tier
    _is_premium = get_subscription_tier(user_id) == "premium"

    _is_holiday, _days_until_school, _school_start_date = _holiday_info()

    return render_template(
        "index.html",
        error=None,
        subjects=subjects,
        marks_error=marks_error,
        substitutions=substitutions,
        subs_error=subs_error,
        chart_data_json=json.dumps(chart_datasets, ensure_ascii=False),
        user_projects=get_user_projects(user_id),
        display_name=display_name,
        is_premium=_is_premium,
        show_wrap=_show_wrap,
        is_holiday=_is_holiday,
        days_until_school=_days_until_school,
        school_start_date=_school_start_date,
    )
