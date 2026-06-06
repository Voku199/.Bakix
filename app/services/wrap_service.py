import datetime
import logging

from app.database.connection import get_connection

log = logging.getLogger(__name__)

_HALF_YEAR_DAYS = 182

_WEEKDAY_NAMES = ["neděle", "pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota"]


def _since() -> str:
    return (datetime.date.today() - datetime.timedelta(days=_HALF_YEAR_DAYS)).isoformat()


def log_activity(user_id: str, event_type: str) -> None:
    try:
        with get_connection() as db:
            db.execute(
                "INSERT INTO activity_log (user_id, event_type) VALUES (?, ?)",
                (user_id, event_type),
            )
    except Exception:
        log.debug("log_activity failed silently for user=%.8s event=%s", user_id, event_type)


def generate_wrap_for_user(user_id: str) -> dict:
    """Aggregate Bakix usage stats for the past 6 months."""
    since = _since()
    with get_connection() as db:
        total_ai = db.execute(
            "SELECT COUNT(*) FROM conversation_history "
            "WHERE user_id=? AND role='user' AND timestamp >= ?",
            (user_id, since),
        ).fetchone()[0]

        hour_row = db.execute(
            "SELECT CAST(strftime('%H', timestamp) AS INTEGER) AS h, COUNT(*) AS c "
            "FROM conversation_history "
            "WHERE user_id=? AND role='user' AND timestamp >= ? "
            "GROUP BY h ORDER BY c DESC LIMIT 1",
            (user_id, since),
        ).fetchone()

        weekday_row = db.execute(
            "SELECT CAST(strftime('%w', timestamp) AS INTEGER) AS wd, COUNT(*) AS c "
            "FROM conversation_history "
            "WHERE user_id=? AND role='user' AND timestamp >= ? "
            "GROUP BY wd ORDER BY c DESC LIMIT 1",
            (user_id, since),
        ).fetchone()

        active_days = db.execute(
            "SELECT COUNT(DISTINCT date(timestamp)) FROM conversation_history "
            "WHERE user_id=? AND timestamp >= ?",
            (user_id, since),
        ).fetchone()[0]

        marks_count = db.execute(
            "SELECT COUNT(*) FROM activity_log "
            "WHERE user_id=? AND event_type='marks_checked' AND created_at >= ?",
            (user_id, since),
        ).fetchone()[0]

        hw_count = db.execute(
            "SELECT COUNT(*) FROM activity_log "
            "WHERE user_id=? AND event_type='homeworks_checked' AND created_at >= ?",
            (user_id, since),
        ).fetchone()[0]

        komens_count = db.execute(
            "SELECT COUNT(*) FROM activity_log "
            "WHERE user_id=? AND event_type='komens_checked' AND created_at >= ?",
            (user_id, since),
        ).fetchone()[0]

    most_active_hour = hour_row[0] if hour_row else None
    most_active_weekday = _WEEKDAY_NAMES[weekday_row[0]] if weekday_row else None

    features = [("Známky", marks_count), ("Úkoly", hw_count), ("Zprávy", komens_count)]
    top_feature = max(features, key=lambda x: x[1])[0]

    hour_label = None
    if most_active_hour is not None:
        h = most_active_hour
        hour_label = f"{h:02d}:00–{h:02d}:59"

    today = datetime.date.today()
    period_label = "leden–červen" if today.month <= 6 else "červenec–prosinec"

    return {
        "total_ai_messages":  total_ai,
        "most_active_hour":   most_active_hour,
        "most_active_hour_label": hour_label,
        "most_active_weekday": most_active_weekday,
        "active_days":        active_days,
        "marks_checked":      marks_count,
        "homeworks_checked":  hw_count,
        "komens_checked":     komens_count,
        "top_feature":        top_feature,
        "period_label":       period_label,
        "year":               today.year,
    }
