"""Bakaláři data endpoints: homework, Komens, absences, marks, themes,
timetable and the dashboard today/tomorrow aggregates."""

import datetime
import logging
import re
import threading

from flask import session, request, jsonify
from flask_babel import gettext as _

from app.database.db import get_settings as _db_get_settings, cache_get, cache_set
from app.extensions import limiter
from app.routes.bakalari import bakalari_bp
from app.routes.bakalari.helpers import (
    _HTML_ENTITY_RE, _HTML_TAG_RE, _SEEN_TTL, _get_svc_and_token, _notify_substitutions, _push_svc,
)
from app.services.bakalari import BakalariService
from app.services import demo_data as _demo
from app.services.wrap_service import log_activity

log = logging.getLogger(__name__)

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
            return jsonify({"error": _("Nepodařilo se načíst úkoly (%(code)s)", code=data.get('status_code', ''))}), 502

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
        return jsonify({"error": _("Interní chyba serveru")}), 500


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
            return jsonify({"error": _("Nepodařilo se načíst zprávy (%(code)s)", code=data.get('status_code', ''))}), 502

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
        return jsonify({"error": _("Interní chyba serveru")}), 500


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
            return jsonify({"error": _("Nepodařilo se načíst příjemce (%(code)s)", code=data.get('status_code', ''))}), 502
        recipients = [
            {"code": r.get("Code"), "name": r.get("Name") or r.get("DisplayName")}
            for r in (data.get("Recipients") or [])
            if r.get("Code")
        ]
        return jsonify({"recipients": recipients})
    except Exception:
        log.exception("api_komens_message_types: unexpected error")
        return jsonify({"error": _("Interní chyba serveru")}), 500


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
            return jsonify({"error": _("Chybí příjemce, předmět nebo text.")}), 400
        result = svc.send_komens_message(token, recipient_id, subject, content)
        if result.get("status_code") == 401:
            token = svc.reauth(user_id)
            if not token:
                return jsonify({"error": "Not authenticated"}), 401
            result = svc.send_komens_message(token, recipient_id, subject, content)
            if result.get("status_code") == 401:
                return jsonify({"error": "Not authenticated"}), 401
        if "error" in result:
            return jsonify({"error": result.get("error", _("Odeslání selhalo."))}), 502
        return jsonify({"ok": True})
    except Exception:
        log.exception("api_komens_send: unexpected error")
        return jsonify({"error": _("Interní chyba serveru")}), 500


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
            return jsonify({"error": _("Nepodařilo se načíst absence (%(code)s)", code=data.get('status_code', ''))}), 502

        cache_set(user_id, "absences", data)
        return jsonify(data)
    except Exception:
        log.exception("api_absences: unexpected error")
        return jsonify({"error": _("Interní chyba serveru")}), 500


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
            return jsonify({"error": _("Nepodařilo se načíst známky (%(code)s)", code=data.get('status_code', ''))}), 502

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
        return jsonify({"error": _("Interní chyba serveru")}), 500


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
            return jsonify({"error": _("Nepodařilo se načíst témata (%(code)s)", code=data.get('status_code', ''))}), 502

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
        return jsonify({"error": _("Interní chyba serveru")}), 500


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
            return jsonify({"error": _("Nepodařilo se načíst rozvrh (%(code)s)", code=data.get('status_code', ''))}), 502

        return jsonify(data)
    except Exception:
        log.exception("get_today_timetable: unexpected error")
        return jsonify({"error": _("Interní chyba serveru")}), 500


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
            return jsonify({"error": _("Nepodařilo se načíst rozvrh (%(code)s)", code=data.get('status_code', ''))}), 502

        if not isinstance(data, dict):
            log.error("api_dashboard_today: unexpected timetable shape: %s", type(data))
            return jsonify({"error": _("Neočekávaná odpověď serveru")}), 502

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
        return jsonify({"error": _("Interní chyba serveru")}), 500


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
            return jsonify({"error": _("Nepodařilo se načíst rozvrh (%(code)s)", code=data.get('status_code', ''))}), 502

        if not isinstance(data, dict):
            return jsonify({"error": _("Neočekávaná odpověď serveru")}), 502

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
        return jsonify({"error": _("Interní chyba serveru")}), 500

