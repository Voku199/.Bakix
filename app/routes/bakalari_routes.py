import datetime
import json
import logging
import os
import re
import uuid

from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify, abort, current_app
from markupsafe import Markup

from app.database.db import fetch_row, get_settings as _db_get_settings, save_settings as _db_save_settings, upsert_all, update_display_name as _db_update_display_name, cache_get, cache_set
from app.services.bakalari import BakalariService
from app.services.crypto import encrypt_json
from app.services.gemini_service import GeminiService

log = logging.getLogger(__name__)

bakalari_bp = Blueprint("bakalari", __name__)

_COLORS = [
    "#b5451b", "#2d6a4f", "#5c7a9e", "#8b6b3d",
    "#7a4f7a", "#4a7c6b", "#c47d2e", "#5e7a5e",
]


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


_HTML_TAG_RE = re.compile(r'<[^>]+>')


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
def api_homeworks():
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

        today   = datetime.date.today()
        _ck     = f"hw_{today}"
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
        cache_set(user_id, _ck, homeworks)
        return jsonify(homeworks)
    except Exception:
        log.exception("api_homeworks: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/3/komens/messages/received", methods=["POST"])
def api_komens():
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

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

        top3 = sorted(
            (data.get("Messages") if isinstance(data, dict) else []) or [],
            key=lambda m: m.get("SentDate") or "",
            reverse=True,
        )[:3]

        result = [
            {
                "Id":       m.get("Id"),
                "Title":    m.get("Title"),
                "Sender":   (m.get("Sender") or {}).get("Name"),
                "SentDate": m.get("SentDate"),
                "Read":     bool(m.get("Read")),
                "Text":     _HTML_TAG_RE.sub("", m.get("Text") or "")[:80],
            }
            for m in top3
        ]
        cache_set(user_id, "komens", result)
        return jsonify(result)
    except Exception:
        log.exception("api_komens: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/3/marks", methods=["GET"])
def api_marks():
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

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
        cache_set(user_id, "marks", subjects)
        return jsonify(subjects)
    except Exception:
        log.exception("api_marks: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/3/timetable/actual", methods=["GET"])
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
def api_dashboard_today():
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

        # ── Build O(1) lookup dicts from the top-level helper arrays ──────────
        subjects = {s["Id"]: s["Name"]   for s in (data.get("Subjects") or []) if s.get("Id")}
        teachers = {t["Id"]: t["Name"]   for t in (data.get("Teachers") or []) if t.get("Id")}
        rooms    = {r["Id"]: r["Abbrev"] for r in (data.get("Rooms")    or []) if r.get("Id")}
        hours    = {
            h["Id"]: f"{h['BeginTime'][:5]}-{h['EndTime'][:5]}"
            for h in (data.get("Hours") or [])
            if h.get("Id") and h.get("BeginTime") and h.get("EndTime")
        }

        # ── Locate today's day block ──────────────────────────────────────────
        today = datetime.date.today().isoformat()
        today_day = next(
            (d for d in (data.get("Days") or []) if (d.get("Date") or "").startswith(today)),
            None,
        )
        if not today_day:
            return jsonify([])

        # ── Map atoms to output records ───────────────────────────────────────
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
        cache_set(user_id, "tt_today", result)
        return jsonify(result)
    except Exception:
        log.exception("api_dashboard_today: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/dashboard/tomorrow", methods=["GET"])
def api_dashboard_tomorrow():
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
        cache_set(user_id, "tt_tomorrow", result)
        return jsonify(result)
    except Exception:
        log.exception("api_dashboard_tomorrow: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/settings", methods=["GET"])
def api_settings_get():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    return jsonify(_db_get_settings(user_id))


@bakalari_bp.route("/api/settings", methods=["POST"])
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

    _db_save_settings(user_id, data)
    return jsonify({"ok": True})


@bakalari_bp.route("/api/gemini/insights", methods=["GET"])
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


# ── Generated-page helpers ────────────────────────────────────────────────────

def _gen_dir() -> str:
    return os.path.join(current_app.instance_path, "generated")


def _gen_index_path() -> str:
    return os.path.join(_gen_dir(), "index.json")


def _load_gen_index() -> dict:
    path = _gen_index_path()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _save_gen_index(index: dict) -> None:
    os.makedirs(_gen_dir(), exist_ok=True)
    with open(_gen_index_path(), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)


def get_user_projects(user_id: str) -> list:
    """Return [{page_id, topic}] for all generated pages owned by user_id."""
    index = _load_gen_index()
    return [
        {"page_id": page_id, "topic": meta.get("title") or "AI obsah"}
        for page_id, meta in index.items()
        if meta.get("user_id") == user_id
    ]


@bakalari_bp.route("/api/user/settings", methods=["POST"])
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
def api_ai_pages():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    return jsonify(get_user_projects(user_id))


# ── AI chat endpoint (structured response with optional page generation) ─────

@bakalari_bp.route("/api/ai/chat", methods=["POST"])
def api_ai_chat():
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Not authenticated"}), 401

        body      = request.get_json(force=True, silent=True) or {}
        message   = (body.get("message") or "").strip()
        chat_mode = (body.get("chat_mode") or "auto").strip().lower()

        if not message:
            return jsonify({"error": "Prázdná zpráva"}), 400

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
                ai_result = gemini.handle_grades_context(user_id, flat_grades, message)
            else:
                ai_result = gemini.get_response(user_id, message, student_data)
        except ValueError:
            return jsonify({"error": "GEMINI_API_KEY not configured"}), 503

        action_url = None
        html_body  = ai_result.get("page_content_html") or ""
        if ai_result.get("intent") == "create_page" and html_body.strip():
            page_id   = uuid.uuid4().hex
            page_path = os.path.join(_gen_dir(), f"{page_id}.html")
            os.makedirs(_gen_dir(), exist_ok=True)
            with open(page_path, "w", encoding="utf-8") as f:
                f.write(html_body)
            index = _load_gen_index()
            index[page_id] = {
                "user_id": user_id,
                "title":   ai_result.get("page_title") or "AI obsah",
            }
            _save_gen_index(index)
            action_url = f"/api/ai/generated/{page_id}"

        return jsonify({
            "message":      ai_result.get("message", ""),
            "action_url":   action_url,
            "action_label": ai_result.get("action_label") if action_url else None,
            "is_test":      bool(ai_result.get("is_test", False)),
            "sender":       "ai",
            "timestamp":    datetime.datetime.utcnow().isoformat() + "Z",
        })
    except Exception:
        log.exception("api_ai_chat: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/ai/generated/<page_id>", methods=["GET"])
def api_ai_generated(page_id):
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("welcome"))

    # Strict validation: uuid4().hex is 32 lowercase hex chars
    if not page_id.isalnum() or len(page_id) > 32:
        return abort(404)

    index = _load_gen_index()
    meta  = index.get(page_id)
    if not meta or meta.get("user_id") != user_id:
        return abort(404)

    page_path = os.path.join(_gen_dir(), f"{page_id}.html")
    if not os.path.isfile(page_path):
        return abort(404)

    with open(page_path, encoding="utf-8") as f:
        raw_html = f.read()

    return render_template(
        "generated_page.html",
        title=meta.get("title", "AI obsah"),
        content=Markup(raw_html),
        page_id=page_id,
    )


@bakalari_bp.route("/api/ai/generated/<page_id>", methods=["PUT"])
def api_ai_generated_update(page_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    if not page_id.isalnum() or len(page_id) > 32:
        return abort(404)

    index = _load_gen_index()
    meta  = index.get(page_id)
    if not meta or meta.get("user_id") != user_id:
        return abort(404)

    body    = request.get_json(force=True, silent=True) or {}
    content = (body.get("content") or "").strip()
    if not content:
        return jsonify({"error": "Prázdný obsah"}), 400

    page_path = os.path.join(_gen_dir(), f"{page_id}.html")
    with open(page_path, "w", encoding="utf-8") as f:
        f.write(content)
    return jsonify({"ok": True})


@bakalari_bp.route("/api/ai/generated/<page_id>", methods=["DELETE"])
def api_ai_generated_delete(page_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    if not page_id.isalnum() or len(page_id) > 32:
        return abort(404)

    index = _load_gen_index()
    meta  = index.get(page_id)
    if not meta or meta.get("user_id") != user_id:
        return abort(404)

    page_path = os.path.join(_gen_dir(), f"{page_id}.html")
    if os.path.isfile(page_path):
        os.remove(page_path)

    del index[page_id]
    _save_gen_index(index)
    return jsonify({"ok": True})


@bakalari_bp.route("/api/ai/regen/<page_id>", methods=["POST"])
def api_ai_regen(page_id):
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Not authenticated"}), 401

        if not page_id.isalnum() or len(page_id) > 32:
            return abort(404)

        index = _load_gen_index()
        meta  = index.get(page_id)
        if not meta or meta.get("user_id") != user_id:
            return abort(404)

        page_path = os.path.join(_gen_dir(), f"{page_id}.html")
        if not os.path.isfile(page_path):
            return abort(404)

        with open(page_path, encoding="utf-8") as f:
            current_html = f.read()

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
            new_html = GeminiService().regenerate_page(current_html, prompt, student_data)
        except ValueError:
            return jsonify({"error": "GEMINI_API_KEY not configured"}), 503

        with open(page_path, "w", encoding="utf-8") as f:
            f.write(new_html)
        return jsonify({"ok": True})
    except Exception:
        log.exception("api_ai_regen: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/api/ai/modify/<page_id>", methods=["POST"])
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

        index = _load_gen_index()
        meta  = index.get(page_id)
        if not meta or meta.get("user_id") != user_id:
            return abort(404)

        page_path = os.path.join(_gen_dir(), f"{page_id}.html")
        if not os.path.isfile(page_path):
            return abort(404)

        body   = request.get_json(force=True, silent=True) or {}
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            return jsonify({"error": "Prázdný požadavek"}), 400

        with open(page_path, encoding="utf-8") as f:
            current_html = f.read()

        try:
            new_html = GeminiService().modify_page(user_id, current_html, prompt)
        except ValueError:
            return jsonify({"error": "GEMINI_API_KEY not configured"}), 503

        with open(page_path, "w", encoding="utf-8") as f:
            f.write(new_html)

        return jsonify({"ok": True, "page_url": f"/api/ai/generated/{page_id}"})
    except Exception:
        log.exception("api_ai_modify: unexpected error")
        return jsonify({"error": "Interní chyba serveru"}), 500


@bakalari_bp.route("/")
def index():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("welcome"))

    row = fetch_row(user_id)
    if not row:
        log.warning("index: no DB row for user=%.8s, clearing session", user_id)
        session.clear()
        return redirect(url_for("welcome"))

    svc   = BakalariService(base_url=row["school_url"])
    token = svc.get_token(user_id)
    if not token:
        session.clear()
        return redirect(url_for("welcome"))

    marks_data = svc.get_marks(token)

    if marks_data.get("status_code") == 401:
        # Token expired — attempt re-authentication from stored credentials
        log.info("index: token expired for user=%.8s, reauthenticating", user_id)
        token = svc.reauth(user_id)
        if not token:
            session.clear()
            return redirect(url_for("welcome"))
        marks_data = svc.get_marks(token)
        if marks_data.get("status_code") == 401:
            log.warning("index: reauth still returned 401 for user=%.8s", user_id)
            session.clear()
            return redirect(url_for("welcome"))

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
    )
