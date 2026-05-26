import datetime
import logging

from apscheduler.schedulers.background import BackgroundScheduler

log = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(timezone="Europe/Prague")


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

    _scheduler.start()
    log.info("scheduler: started (evening reminder 18:00, weekly summary Sun 08:00, cache cleanup 04:00 Europe/Prague)")


def _run_weekly_summaries() -> None:
    from app.services.weekly_summary import run_weekly_summary_for_all
    run_weekly_summary_for_all()


def _run_cache_cleanup() -> None:
    from app.database.db import cache_cleanup_old
    n = cache_cleanup_old(days=7)
    log.info("scheduler: cache_cleanup deleted %d stale rows", n)


def _send_evening_reminders() -> None:
    """Query tomorrow's timetable for every subscribed user and send a push summary."""
    from app.database.connection import get_connection
    from app.database.db import fetch_row
    from app.services.bakalari import BakalariService
    from app.services.push_service import PushNotificationService

    push_svc = PushNotificationService()
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

    with get_connection() as db:
        user_ids = [
            r[0] for r in db.execute(
                "SELECT DISTINCT user_id FROM push_subscriptions"
            ).fetchall()
        ]

    for user_id in user_ids:
        try:
            row = fetch_row(user_id)
            if not row:
                continue

            svc   = BakalariService(base_url=row["school_url"])
            token = svc.get_token(user_id)
            if not token:
                token = svc.reauth(user_id)
            if not token:
                log.warning("scheduler: no token for user=%.8s, skipping", user_id)
                continue

            data = svc.get_timetable(token)
            if "error" in data:
                log.warning("scheduler: timetable error for user=%.8s: %s", user_id, data)
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
                log.info("scheduler: no timetable for tomorrow for user=%.8s", user_id)
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

            push_svc.send_to_user(
                user_id,
                "Bakix – Zítřejší rozvrh",
                f"Zítra máš {count} hodin: {preview}",
            )

        except Exception:
            log.exception("scheduler: evening_reminder failed for user=%.8s", user_id)
