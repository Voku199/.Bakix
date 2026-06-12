"""Shared helpers for the bakalari blueprint: HTML sanitization, holiday
detection, chart building, push-notification dedupe and service lookup."""

import datetime
import logging
import re

from flask import session

from app.database.db import fetch_row, cache_get, cache_set, get_settings as _db_get_settings
from app.services.bakalari import BakalariService
from app.services.push_service import PushNotificationService

log = logging.getLogger(__name__)

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



# ── Generated-page helpers ────────────────────────────────────────────────────────────────

def get_user_projects(user_id: str) -> list:
    """Return [{page_id, topic}] for all generated pages owned by user_id."""
    from app.database.db import list_generated_pages
    return list_generated_pages(user_id)

