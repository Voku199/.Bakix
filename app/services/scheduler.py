import datetime
import logging
import re

from apscheduler.schedulers.background import BackgroundScheduler

log = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(timezone="Europe/Prague")

_SEEN_TTL    = 2_592_000          # 30 days
_HTML_TAG_RE = re.compile(r'<[^>]+>')
_HTML_ENT_RE = re.compile(r'&(?:nbsp|amp|lt|gt|quot|apos|#\d+|#x[\da-fA-F]+);')

_SUB_LABELS = {
    'Cancelled':     'Odpadlo',
    'Substitution':  'Suplování',
    'TeacherChange': 'Náhradník',
    'RoomChange':    'Jiná učebna',
    'Absent':        'Absence',
}

# ── Adaptive poll interval ─────────────────────────────────────────────────────
# Called once per scheduler tick to decide the current global poll window.
# All users share the same interval — no per-user setting.
#
# Rationale for the windows:
#   School hours  → teachers enter grades / komens actively, be fast (3 min)
#   After school  → grades still arrive, homework deadlines update (5 min)
#   Evening/night → almost nothing changes, be gentle on Bakaláře (30 min)
#   Weekend day   → occasional updates from diligent teachers (10 min)
#   Weekend night → nothing, save API quota (30 min)

_PRAGUE_TZ = datetime.timezone(datetime.timedelta(hours=1))   # CET; DST handled by APScheduler

def _adaptive_interval_minutes() -> int:
    now     = datetime.datetime.now()   # scheduler timezone is Europe/Prague
    hour    = now.hour
    weekday = now.weekday()             # 0=Mon … 6=Sun

    if weekday >= 5:                    # weekend
        return 10 if 7 <= hour < 22 else 30

    # weekday
    if 7 <= hour < 16:   return 3      # school hours
    if 16 <= hour < 22:  return 5      # after school / teacher grading time
    return 30                          # night


def start_scheduler(app) -> None:
    """Register jobs and start the background scheduler. Safe to call multiple times."""
    if _scheduler.running:
        return

    @_scheduler.scheduled_job("cron", hour=18, minute=0, id="evening_reminder")
    def evening_reminder():
        with app.app_context():
            _send_evening_reminders()

    @_scheduler.scheduled_job("cron", day_of_week="sun", hour=8, minute=0, id="weekly_summary")
    def weekly_summary_job():
        with app.app_context():
            _run_weekly_summaries()

    @_scheduler.scheduled_job("cron", hour=4, minute=0, id="cache_cleanup")
    def cache_cleanup_job():
        with app.app_context():
            _run_cache_cleanup()

    @_scheduler.scheduled_job("cron", month="6,12", day=1, hour=9, minute=0, id="wrap_push")
    def wrap_push_job():
        with app.app_context():
            _send_wrap_push_notifications()

    @_scheduler.scheduled_job("cron", month="7,8", day_of_week="mon", hour=9, minute=0, id="summer_countdown")
    def summer_countdown_job():
        with app.app_context():
            _send_summer_countdown()

    @_scheduler.scheduled_job("cron", month="7,8", day_of_week="wed", hour=3, minute=0, id="token_keeper")
    def token_keeper_job():
        with app.app_context():
            _refresh_all_tokens()

    @_scheduler.scheduled_job("interval", minutes=1, id="bakalari_poll")
    def bakalari_poll():
        with app.app_context():
            _poll_all_users()

    _scheduler.start()
    log.info(
        "scheduler: started "
        "(adaptive poll every 1 min tick, interval 3–30 min by time of day; "
        "evening reminder 18:00, weekly summary Sun 08:00, "
        "cache cleanup 04:00, wrap push Jun/Dec 1st 09:00 Europe/Prague)"
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _subscribed_user_ids() -> list:
    from app.database.connection import get_connection
    with get_connection() as db:
        return [
            r[0] for r in db.execute(
                "SELECT DISTINCT user_id FROM push_subscriptions"
            ).fetchall()
        ]


def _get_svc_and_token(user_id: str):
    """Return (BakalariService, token) for user_id, attempting reauth if needed."""
    from app.database.db import fetch_row
    from app.services.bakalari import BakalariService

    row = fetch_row(user_id)
    if not row:
        return None, None
    svc   = BakalariService(base_url=row["school_url"])
    token = svc.get_token(user_id) or svc.reauth(user_id)
    return svc, token


def _clean_text(raw: str) -> str:
    text = _HTML_TAG_RE.sub("", raw)
    text = _HTML_ENT_RE.sub(" ", text)
    return " ".join(text.split())


# ── Jobs ──────────────────────────────────────────────────────────────────────

def _run_weekly_summaries() -> None:
    from app.services.weekly_summary import run_weekly_summary_for_all
    run_weekly_summary_for_all()


def _run_cache_cleanup() -> None:
    from app.database.db import cache_cleanup_old
    n = cache_cleanup_old(days=7)
    log.info("scheduler: cache_cleanup deleted %d stale rows", n)


def _send_evening_reminders() -> None:
    """Query tomorrow's timetable for every subscribed user and send a push summary."""
    from app.services.push_service import PushNotificationService

    push_svc = PushNotificationService()
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

    for user_id in _subscribed_user_ids():
        try:
            svc, token = _get_svc_and_token(user_id)
            if not token:
                log.warning("scheduler: evening: no token for user=%.8s", user_id)
                continue

            data = svc.get_timetable(token)
            if data.get("status_code") == 401:
                token = svc.reauth(user_id)
                if not token:
                    continue
                data = svc.get_timetable(token)
            if "error" in data:
                log.warning("scheduler: evening: timetable error user=%.8s: %s", user_id, data)
                continue

            subjects_map = {
                s["Id"]: s["Name"]
                for s in (data.get("Subjects") or [])
                if s.get("Id")
            }

            tomorrow_day = next(
                (d for d in (data.get("Days") or []) if (d.get("Date") or "").startswith(tomorrow)),
                None,
            )
            if not tomorrow_day:
                continue

            lessons = [
                subjects_map[a["SubjectId"]]
                for a in (tomorrow_day.get("Atoms") or [])
                if a.get("SubjectId") and a["SubjectId"] in subjects_map
            ]
            if not lessons:
                continue

            count   = len(lessons)
            preview = ", ".join(lessons[:3])
            if count > 3:
                preview += f" a {count - 3} dalších"

            from app.database.db import get_settings as _get_settings
            if _get_settings(user_id).get("notifications_daily") is False:
                continue
            push_svc.send_to_user(
                user_id,
                "Bakix – Zítřejší rozvrh",
                f"Zítra máš {count} hodin: {preview}",
                url="/#events-body",
                tag="bakix-schedule",
            )

        except Exception:
            log.exception("scheduler: evening: failed for user=%.8s", user_id)


def _poll_substitutions(user_id: str, svc, token, push_svc) -> "str | None":
    """Check today's and tomorrow's timetable for new substitutions; push if found."""
    from app.database.db import cache_get, cache_set

    tt_data = svc.get_timetable(token)
    if tt_data.get("status_code") == 401:
        token = svc.reauth(user_id)
        if not token:
            return None
        tt_data = svc.get_timetable(token)
    if "error" in tt_data:
        log.warning("scheduler: subs: timetable error user=%.8s: %s", user_id, tt_data)
        return token

    today_str    = datetime.date.today().isoformat()
    tomorrow_str = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    target_dates = {today_str, tomorrow_str}

    subjects_map = {s["Id"]: s["Name"] for s in (tt_data.get("Subjects") or []) if s.get("Id")}
    hours_map    = {
        h["Id"]: f"{h['BeginTime'][:5]}-{h['EndTime'][:5]}"
        for h in (tt_data.get("Hours") or [])
        if h.get("Id") and h.get("BeginTime") and h.get("EndTime")
    }

    seen_ids    = set(cache_get(user_id, "push_seen_subs", ttl=_SEEN_TTL) or [])
    all_sub_ids: set = set()

    for day in (tt_data.get("Days") or []):
        date_str = (day.get("Date") or "")[:10]
        if date_str not in target_dates:
            continue
        when = "dnes" if date_str == today_str else "zítra"

        novel = []
        for atom in (day.get("Atoms") or []):
            change = atom.get("Change")
            if change is None:
                continue
            change_type = change.get("ChangeType") or "Change"
            sub_id      = f"{date_str}:{atom.get('HourId')}:{change_type}"
            all_sub_ids.add(sub_id)
            if sub_id in seen_ids:
                continue
            novel.append({
                "subject":     subjects_map.get(atom.get("SubjectId"), "hodina"),
                "time":        hours_map.get(atom.get("HourId"), "—"),
                "change_type": change_type,
            })

        if novel:
            from app.database.db import get_settings as _get_settings_subs
            if _get_settings_subs(user_id).get("notifications_subs") is not False:
                first = novel[0]
                count = len(novel)
                label = _SUB_LABELS.get(first["change_type"], "Změna")
                body  = (
                    f"{label} {when}: {first['subject']} ({first['time']})"
                    if count == 1 else
                    f"{count} změn v rozvrhu {when} (první: {first['subject']})"
                )
                push_svc.send_to_user(user_id, "Změna v rozvrhu", body, url="/#events-body", tag="bakix-subs")
                log.info("scheduler: subs: push user=%.8s when=%s novel=%d", user_id, when, count)

    updated = seen_ids | all_sub_ids
    if updated != seen_ids:
        cache_set(user_id, "push_seen_subs", list(updated))
    return token


def _poll_marks(user_id: str, svc, token, push_svc, prefs: dict) -> "str | None":
    """Check for new grades; push if found. Returns token (possibly refreshed) or None."""
    from app.database.db import cache_get, cache_set

    if prefs.get("notifications_grades") is False:
        return token

    marks_data = svc.get_marks(token)
    if marks_data.get("status_code") == 401:
        token = svc.reauth(user_id)
        if not token:
            return None
        marks_data = svc.get_marks(token)
    if "error" in marks_data:
        log.warning("scheduler: marks: error user=%.8s: %s", user_id, marks_data)
        return token

    # Build a fingerprint for every mark: abbrev:marktext:date
    current_ids: set[str] = set()
    novel_marks: list[dict] = []

    seen_ids = set(cache_get(user_id, "push_seen_marks", ttl=_SEEN_TTL) or [])

    for subject in (marks_data.get("Subjects") or []):
        subj_info = subject.get("Subject") or {}
        abbrev    = subj_info.get("Abbrev", "?").strip()
        name      = subj_info.get("Name", abbrev)
        for mark in (subject.get("Marks") or []):
            mark_text = (mark.get("MarkText") or "").strip()
            mark_date = (mark.get("MarkDate") or "")[:10]
            fprint    = f"{abbrev}:{mark_text}:{mark_date}"
            current_ids.add(fprint)
            if fprint not in seen_ids:
                novel_marks.append({
                    "subject": name,
                    "abbrev":  abbrev,
                    "text":    mark_text,
                    "date":    mark_date,
                    "caption": (mark.get("Caption") or "").strip(),
                })

    if novel_marks:
        count = len(novel_marks)
        first = novel_marks[0]
        if count == 1:
            caption = f" ({first['caption']})" if first["caption"] else ""
            body    = f"{first['subject']}: {first['text']}{caption}"
        else:
            body = f"{count} nových známek (první: {first['subject']} {first['text']})"
        push_svc.send_to_user(user_id, "Nová známka v Bakixu", body, url="/#marks-body", tag="bakix-marks")
        log.info("scheduler: marks: push user=%.8s novel=%d", user_id, count)

    updated = seen_ids | current_ids
    if updated != seen_ids:
        cache_set(user_id, "push_seen_marks", list(updated))
    return token


def _send_wrap_push_notifications() -> None:
    """Send a Bakix Wrap notification to all subscribed users on June 1 and December 1."""
    from app.services.push_service import PushNotificationService

    push_svc = PushNotificationService()
    month    = datetime.date.today().month
    period   = "první pololetí" if month == 6 else "druhé pololetí"
    user_ids = _subscribed_user_ids()
    for user_id in user_ids:
        try:
            push_svc.send_to_user(
                user_id,
                "Bakix Wrap je tady! ✦",
                f"Podívej se na svoje statistiky za {period} — otevři Bakix!",
                url="/wrap",
                tag="bakix-wrap",
            )
        except Exception:
            log.exception("scheduler: wrap push: failed for user=%.8s", user_id)
    log.info("scheduler: wrap push sent to %d users", len(user_ids))


def _send_summer_countdown() -> None:
    """Every Monday in July/August, push the countdown until school starts."""
    from app.services.push_service import PushNotificationService

    today = datetime.date.today()
    if today.month not in (7, 8):
        return

    school_start = datetime.date(today.year, 9, 1)
    while school_start.weekday() >= 5:
        school_start += datetime.timedelta(days=1)
    days_left = (school_start - today).days

    if days_left == 1:
        body = "Zítra začíná škola! Připrav si aktovku."
    elif days_left <= 7:
        body = f"Ještě {days_left} dní prázdnin — hurá!"
    elif days_left <= 14:
        body = f"Do školy zbývá {days_left} dní. Uži si zbytek prázdnin!"
    else:
        body = f"Do školy zbývá {days_left} dní. Léto ještě nekončí!"

    push_svc = PushNotificationService()
    user_ids = _subscribed_user_ids()
    for user_id in user_ids:
        try:
            push_svc.send_to_user(
                user_id,
                f"Do školy zbývá {days_left} dní",
                body,
                url="/",
                tag="bakix-summer",
            )
        except Exception:
            log.exception("scheduler: summer_countdown: failed for user=%.8s", user_id)
    log.info("scheduler: summer_countdown sent to %d users (days_left=%d)", len(user_ids), days_left)


def _refresh_all_tokens() -> None:
    """Every Wednesday in July/August, reauthenticate all users so tokens don't expire over summer.

    Without this, students who don't open Bakix all summer would face a forced re-login in September
    because the Bakaláře refresh token TTL is typically 30–90 days.
    """
    today = datetime.date.today()
    if today.month not in (7, 8):
        return

    from app.database.connection import get_connection
    from app.database.db import fetch_row
    from app.services.bakalari import BakalariService

    with get_connection() as db:
        user_ids = [r[0] for r in db.execute("SELECT user_id FROM saved_credentials").fetchall()]

    refreshed = 0
    failed = 0
    for user_id in user_ids:
        try:
            row = fetch_row(user_id)
            if not row:
                continue
            svc   = BakalariService(base_url=row["school_url"])
            token = svc.reauth(user_id)
            if token:
                refreshed += 1
            else:
                failed += 1
                log.warning("scheduler: token_keeper: reauth failed for user=%.8s", user_id)
        except Exception:
            failed += 1
            log.exception("scheduler: token_keeper: error for user=%.8s", user_id)

    log.info("scheduler: token_keeper: refreshed=%d failed=%d", refreshed, failed)


def _poll_all_users() -> None:
    """Tick every minute; actually poll Bakaláře only when the adaptive window has elapsed.

    In July/August (summer holidays), homework, komens and substitutions are skipped;
    only marks are polled so students with retake exams still get grade notifications.
    """
    from app.database.db import cache_get, cache_set, get_settings
    from app.services.push_service import PushNotificationService

    interval_min = _adaptive_interval_minutes()
    push_svc     = PushNotificationService()
    now          = datetime.datetime.utcnow()
    today        = datetime.date.today()
    to_date      = today + datetime.timedelta(days=7)
    _is_summer   = today.month in (7, 8)

    for user_id in _subscribed_user_ids():
        try:
            # ── Adaptive throttle ─────────────────────────────────────────────
            last_ts = cache_get(user_id, "poll_last_checked", ttl=172800)  # 2-day TTL
            if last_ts:
                elapsed_sec = (now - datetime.datetime.fromisoformat(last_ts)).total_seconds()
                if elapsed_sec < interval_min * 60:
                    continue
            cache_set(user_id, "poll_last_checked", now.isoformat())

            svc, token = _get_svc_and_token(user_id)
            if not token:
                log.warning("scheduler: poll: no token for user=%.8s", user_id)
                continue

            log.debug(
                "scheduler: poll: running for user=%.8s (interval=%dmin)",
                user_id, interval_min,
            )

            prefs = get_settings(user_id)

            # ── Homework (skipped in summer — schools have no assignments) ────
            if not _is_summer:
                hw_data = svc.get_homeworks(token, today.isoformat(), to_date.isoformat())
                if hw_data.get("status_code") == 401:
                    token = svc.reauth(user_id)
                    if not token:
                        continue
                    hw_data = svc.get_homeworks(token, today.isoformat(), to_date.isoformat())

                if "error" not in hw_data:
                    homeworks = [
                        {
                            "ID":      hw.get("Id"),
                            "Subject": (hw.get("Subject") or {}).get("Name"),
                            "DateEnd": hw.get("DateEnd"),
                        }
                        for hw in (hw_data.get("Homeworks") if isinstance(hw_data, dict) else []) or []
                        if not hw.get("Closed") and not hw.get("Done")
                    ]
                    hw_ids = {str(h["ID"]) for h in homeworks if h["ID"]}
                    if hw_ids:
                        seen_ids  = set(cache_get(user_id, "push_seen_hw", ttl=_SEEN_TTL) or [])
                        novel_ids = hw_ids - seen_ids
                        if novel_ids and prefs.get("notifications_homeworks") is not False:
                            first = next((h for h in homeworks if str(h["ID"]) in novel_ids), None)
                            count = len(novel_ids)
                            if count == 1 and first:
                                due  = (first["DateEnd"] or "")[:10]
                                subj = first["Subject"] or "předmět"
                                body = f"{subj} – odevzdat do {due}"
                            else:
                                body = f"Máš {count} nových úkolů"
                            push_svc.send_to_user(user_id, "Nový úkol v Bakixu", body, url="/#homeworks-body", tag="bakix-hw")
                            log.info("scheduler: poll: hw push user=%.8s novel=%d", user_id, len(novel_ids))
                        cache_set(user_id, "push_seen_hw", list(hw_ids | seen_ids))

            # ── Komens (skipped in summer — teachers rarely message in Jul/Aug) ─
            if not _is_summer:
                komens_data = svc.get_komens(token)
                if komens_data.get("status_code") == 401:
                    token = svc.reauth(user_id)
                    if not token:
                        continue
                    komens_data = svc.get_komens(token)

                if "error" not in komens_data:
                    top5 = sorted(
                        (komens_data.get("Messages") if isinstance(komens_data, dict) else []) or [],
                        key=lambda m: m.get("SentDate") or "",
                        reverse=True,
                    )[:5]
                    messages = [
                        {
                            "Id":     m.get("Id"),
                            "Title":  m.get("Title"),
                            "Sender": (m.get("Sender") or {}).get("Name"),
                            "Read":   bool(m.get("Read")),
                            "Text":   _clean_text(m.get("Text") or ""),
                        }
                        for m in top5
                    ]
                    msg_ids = {str(m["Id"]) for m in messages if m["Id"]}
                    if msg_ids:
                        seen_ids     = set(cache_get(user_id, "push_seen_komens", ttl=_SEEN_TTL) or [])
                        novel_unread = [m for m in messages if str(m["Id"]) not in seen_ids and not m["Read"]]
                        if novel_unread and prefs.get("notifications_messages") is not False:
                            first        = novel_unread[0]
                            sender       = first["Sender"] or "škola"
                            text_preview = (first["Text"] or "")[:80]
                            title_t      = (first["Title"] or "Zpráva")[:60]
                            body         = f"{sender}: {text_preview}" if text_preview else f"{sender}: {title_t}"
                            push_svc.send_to_user(user_id, "Nová zpráva v Bakixu", body, url="/#komens-body", tag="bakix-komens")
                            log.info("scheduler: poll: komens push user=%.8s", user_id)
                        updated = seen_ids | msg_ids
                        if updated != seen_ids:
                            cache_set(user_id, "push_seen_komens", list(updated))

            # ── Marks (always polled — retake exams happen in August) ─────────
            token = _poll_marks(user_id, svc, token, push_svc, prefs)
            if not token:
                continue

            # ── Substitutions (skipped in summer — no timetable) ─────────────
            if not _is_summer:
                _poll_substitutions(user_id, svc, token, push_svc)

        except Exception:
            log.exception("scheduler: poll: failed for user=%.8s", user_id)
