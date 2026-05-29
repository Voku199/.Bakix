import datetime
import logging
import re

from apscheduler.schedulers.background import BackgroundScheduler

log = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(timezone="Europe/Prague")

_SEEN_TTL     = 2_592_000          # 30 days
_HTML_TAG_RE  = re.compile(r'<[^>]+>')
_HTML_ENT_RE  = re.compile(r'&(?:nbsp|amp|lt|gt|quot|apos|#\d+|#x[\da-fA-F]+);')

_SUB_LABELS = {
    'Cancelled':     'Odpadlo',
    'Substitution':  'Suplování',
    'TeacherChange': 'Náhradník',
    'RoomChange':    'Jiná učebna',
    'Absent':        'Absence',
}


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

    @_scheduler.scheduled_job("interval", minutes=1, id="hw_komens_poll")
    def hw_komens_poll():
        with app.app_context():
            _poll_homework_and_komens()

    _scheduler.start()
    log.info(
        "scheduler: started "
        "(hw/komens/subs poll every 1 min with per-user throttle, "
        "evening reminder 18:00, weekly summary Sun 08:00, "
        "cache cleanup 04:00 Europe/Prague)"
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
                tag="bakix-schedule",
            )

        except Exception:
            log.exception("scheduler: evening: failed for user=%.8s", user_id)


def _poll_substitutions(user_id: str, svc, token, push_svc) -> "str | None":
    """Check today's and tomorrow's timetable for new substitutions; push if found.

    Returns the token (possibly refreshed), or None if auth failed.
    """
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

    seen_ids = set(cache_get(user_id, "push_seen_subs", ttl=_SEEN_TTL) or [])
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
                push_svc.send_to_user(user_id, "Změna v rozvrhu", body, tag="bakix-subs")
                log.info("scheduler: subs: push user=%.8s when=%s novel=%d", user_id, when, count)

    updated = seen_ids | all_sub_ids
    if updated != seen_ids:
        cache_set(user_id, "push_seen_subs", list(updated))
    return token


def _poll_homework_and_komens() -> None:
    """Wake every minute; each user is polled only when their configured interval has elapsed."""
    from app.database.db import cache_get, cache_set, get_settings
    from app.services.push_service import PushNotificationService

    push_svc = PushNotificationService()
    now      = datetime.datetime.utcnow()
    today    = datetime.date.today()
    to_date  = today + datetime.timedelta(days=7)

    for user_id in _subscribed_user_ids():
        try:
            # ── Per-user interval throttle ────────────────────────────────────
            prefs        = get_settings(user_id)
            interval_min = max(1, int(prefs.get("poll_interval_minutes") or 30))
            last_ts      = cache_get(user_id, "poll_last_checked", ttl=172800)  # 2-day TTL
            if last_ts:
                elapsed_sec = (now - datetime.datetime.fromisoformat(last_ts)).total_seconds()
                if elapsed_sec < interval_min * 60:
                    continue
            cache_set(user_id, "poll_last_checked", now.isoformat())

            svc, token = _get_svc_and_token(user_id)
            if not token:
                log.warning("scheduler: poll: no token for user=%.8s", user_id)
                continue

            log.debug("scheduler: poll: running for user=%.8s interval=%dmin", user_id, interval_min)

            # ── Homework ──────────────────────────────────────────────────────
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
                        push_svc.send_to_user(user_id, "Nový úkol v Bakixu", body, tag="bakix-hw")
                        log.info("scheduler: poll: hw push user=%.8s novel=%d", user_id, len(novel_ids))
                    cache_set(user_id, "push_seen_hw", list(hw_ids | seen_ids))

            # ── Komens ────────────────────────────────────────────────────────
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
                        push_svc.send_to_user(user_id, "Nová zpráva v Bakixu", body, tag="bakix-komens")
                        log.info("scheduler: poll: komens push user=%.8s", user_id)
                    updated = seen_ids | msg_ids
                    if updated != seen_ids:
                        cache_set(user_id, "push_seen_komens", list(updated))

            # ── Substitutions ─────────────────────────────────────────────────
            _poll_substitutions(user_id, svc, token, push_svc)

        except Exception:
            log.exception("scheduler: poll: failed for user=%.8s", user_id)
