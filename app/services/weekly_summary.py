"""Grade summary helpers: weekly and daily marks → theme lookup → Gemini analysis → push."""

import datetime
import logging

log = logging.getLogger(__name__)

_THEME_CACHE_TTL = 604_800  # 7 days — themes are stable


def _get_grades_in_range(
    subjects: list,
    since: datetime.date,
    until: "datetime.date | None" = None,
) -> list:
    """Return marks within [since, until] (inclusive). until defaults to today."""
    if until is None:
        until = datetime.date.today()
    result = []
    for s in subjects:
        subject_name   = (s.get("Subject") or {}).get("Name", "")
        subject_abbrev = (s.get("Subject") or {}).get("Abbrev", "").strip()
        for m in s.get("Marks") or []:
            raw_date = m.get("EditDate") or ""
            try:
                d = datetime.date.fromisoformat(raw_date[:10])
                if since <= d <= until:
                    result.append({
                        "subject": subject_name,
                        "abbrev":  subject_abbrev,
                        "mark":    m.get("MarkText"),
                        "caption": m.get("Caption") or "",
                        "weight":  m.get("Weight"),
                        "date":    raw_date[:10],
                    })
            except ValueError:
                continue
    return result


def _get_weekly_grades(subjects: list) -> list:
    cutoff = datetime.date.today() - datetime.timedelta(days=7)
    return _get_grades_in_range(subjects, cutoff)


def _get_today_grades(subjects: list) -> list:
    today = datetime.date.today()
    return _get_grades_in_range(subjects, today, today)


def _fetch_marks_with_auth(user_id: str) -> "tuple":
    """Fetch marks for user, handling re-auth.

    Returns (svc, token, subjects) on success, or (None, None, None) on failure.
    """
    from app.database.db import fetch_row
    from app.services.bakalari import BakalariService

    row = fetch_row(user_id)
    if not row:
        log.warning("_fetch_marks_with_auth: no DB row for user=%.8s", user_id)
        return None, None, None

    svc   = BakalariService(base_url=row["school_url"])
    token = svc.get_token(user_id)
    if not token:
        token = svc.reauth(user_id)
    if not token:
        log.warning("_fetch_marks_with_auth: no token for user=%.8s", user_id)
        return None, None, None

    marks_data = svc.get_marks(token)
    if marks_data.get("status_code") == 401:
        token = svc.reauth(user_id)
        if not token:
            return None, None, None
        marks_data = svc.get_marks(token)
    if "error" in marks_data:
        log.warning("_fetch_marks_with_auth: marks error for user=%.8s: %s", user_id, marks_data)
        return None, None, None

    subjects = (marks_data.get("Subjects") if isinstance(marks_data, dict) else []) or []
    return svc, token, subjects


def _fetch_themes(svc, token: str, user_id: str, abbrevs: "list[str]") -> "dict[str, list]":
    """Return {abbrev: [theme_names]}. Uses cache (7-day TTL)."""
    from app.database.db import cache_get, cache_set

    result: dict[str, list] = {}
    for abbrev in abbrevs:
        if not abbrev:
            continue
        cache_key = f"themes_{abbrev}"
        cached = cache_get(user_id, cache_key, ttl=_THEME_CACHE_TTL)
        if cached is not None:
            result[abbrev] = cached
            continue

        data = svc.get_subject_themes(token, abbrev)
        if data.get("status_code") == 401:
            # Token expired mid-loop — caller should reauth; skip this abbrev
            log.warning("_fetch_themes: 401 for abbrev=%s, skipping", abbrev)
            result[abbrev] = []
            continue
        if "error" in data:
            result[abbrev] = []
        else:
            themes_raw = data.get("Themes") or data.get("themes") or []
            names = [
                t.get("Name") or t.get("name") or ""
                for t in themes_raw
                if isinstance(t, dict)
            ]
            result[abbrev] = [n for n in names if n]
            cache_set(user_id, cache_key, result[abbrev])
    return result


def _build_subjects_summary(subjects: list) -> list:
    return [
        {"subject": (s.get("Subject") or {}).get("Name"), "average": s.get("AverageText")}
        for s in subjects
    ]


def _get_weak_subjects(subjects: list) -> list:
    """Return names of subjects whose overall average is ≥ 3.0."""
    result = []
    for s in subjects:
        avg_text = (s.get("AverageText") or "").replace(",", ".")
        try:
            if float(avg_text) >= 3.0:
                name = (s.get("Subject") or {}).get("Name", "")
                if name:
                    result.append(name)
        except ValueError:
            pass
    return result


def _parse_timetable_for_plan(raw: dict) -> list:
    """Extract remaining week schedule as [{"date": ..., "lessons": [...]}]."""
    subj_map = {s["Id"]: s["Name"] for s in (raw.get("Subjects") or []) if s.get("Id")}
    hour_map = {
        h["Id"]: f"{h['BeginTime'][:5]}–{h['EndTime'][:5]}"
        for h in (raw.get("Hours") or [])
        if h.get("Id") and h.get("BeginTime") and h.get("EndTime")
    }
    today  = datetime.date.today()
    result = []
    for day in sorted(raw.get("Days") or [], key=lambda d: d.get("Date", "")):
        date_str = (day.get("Date") or "")[:10]
        try:
            if datetime.date.fromisoformat(date_str) < today:
                continue
        except ValueError:
            continue
        lessons = []
        seen    = set()
        for atom in (day.get("Atoms") or []):
            subj = subj_map.get(atom.get("SubjectId"), "")
            if not subj or subj in seen:
                continue
            seen.add(subj)
            lessons.append({"time": hour_map.get(atom.get("HourId"), ""), "subject": subj})
        if lessons:
            result.append({"date": date_str, "lessons": lessons})
    return result


def _parse_homeworks_for_plan(hw_data: dict) -> list:
    return sorted(
        [
            {
                "subject": (hw.get("Subject") or {}).get("Name", ""),
                "content": (hw.get("Content") or "")[:100],
                "due":     (hw.get("DateEnd") or "")[:10],
            }
            for hw in (hw_data.get("Homeworks") if isinstance(hw_data, dict) else []) or []
            if not hw.get("Closed") and not hw.get("Done")
        ],
        key=lambda h: h["due"],
    )


def generate_weekly_summary_for_user(user_id: str) -> "dict | None":
    """Run the full weekly-summary pipeline for one user.

    Returns the Gemini result dict (with at minimum 'summary' and 'poor_performance'),
    or None on any fatal error.  Push notifications are the caller's responsibility.
    """
    from app.services.gemini_service import GeminiService

    svc, token, subjects = _fetch_marks_with_auth(user_id)
    if subjects is None:
        return None

    weekly_grades = _get_weekly_grades(subjects)

    if not weekly_grades:
        log.info("weekly_summary: no grades this week for user=%.8s", user_id)
        return {"summary": "Tento týden žádné nové známky.", "poor_performance": False,
                "weak_subjects": [], "study_plan": "", "cta": None}

    abbrevs    = list({g["abbrev"] for g in weekly_grades if g["abbrev"]})
    themes_map = _fetch_themes(svc, token, user_id, abbrevs)
    for grade in weekly_grades:
        grade["themes"] = themes_map.get(grade["abbrev"], [])

    try:
        gemini = GeminiService()
        return gemini.generate_weekly_summary(
            user_id=user_id,
            weekly_grades=weekly_grades,
            all_subjects=_build_subjects_summary(subjects),
        )
    except ValueError:
        log.error("weekly_summary: GEMINI_API_KEY not configured")
        return None
    except Exception:
        log.exception("weekly_summary: Gemini call failed for user=%.8s", user_id)
        return None


def generate_daily_summary_for_user(user_id: str) -> "dict | None":
    """Run the daily-summary pipeline for one user (today's marks only).

    Returns the Gemini result dict or None on any fatal error.
    """
    from app.services.gemini_service import GeminiService

    svc, token, subjects = _fetch_marks_with_auth(user_id)
    if subjects is None:
        return None

    daily_grades = _get_today_grades(subjects)

    if not daily_grades:
        log.info("daily_summary: no grades today for user=%.8s", user_id)
        return {"summary": "Dnes žádné nové známky.", "poor_performance": False,
                "weak_subjects": [], "study_plan": "", "cta": None}

    abbrevs    = list({g["abbrev"] for g in daily_grades if g["abbrev"]})
    themes_map = _fetch_themes(svc, token, user_id, abbrevs)
    for grade in daily_grades:
        grade["themes"] = themes_map.get(grade["abbrev"], [])

    try:
        gemini = GeminiService()
        return gemini.generate_daily_summary(
            user_id=user_id,
            daily_grades=daily_grades,
            all_subjects=_build_subjects_summary(subjects),
        )
    except ValueError:
        log.error("daily_summary: GEMINI_API_KEY not configured")
        return None
    except Exception:
        log.exception("daily_summary: Gemini call failed for user=%.8s", user_id)
        return None


def generate_study_plan_for_user(user_id: str) -> "dict | None":
    """Build a personalised study plan from timetable + homeworks + weak subjects."""
    from app.services.gemini_service import GeminiService

    svc, token, subjects = _fetch_marks_with_auth(user_id)
    if subjects is None:
        return None

    # Timetable (current week)
    raw_tt = svc.get_timetable(token)
    if raw_tt.get("status_code") == 401:
        token = svc.reauth(user_id)
        if not token:
            return None
        raw_tt = svc.get_timetable(token)
    if "error" in raw_tt:
        raw_tt = {}

    # Homeworks due in the next 14 days
    today   = datetime.date.today()
    to_date = today + datetime.timedelta(days=14)
    hw_data = svc.get_homeworks(token, today.isoformat(), to_date.isoformat())
    if hw_data.get("status_code") == 401:
        hw_data = {}

    context = {
        "today":          today.isoformat(),
        "timetable":      _parse_timetable_for_plan(raw_tt),
        "homeworks":      _parse_homeworks_for_plan(hw_data),
        "weak_subjects":  _get_weak_subjects(subjects),
    }

    try:
        return GeminiService().generate_study_plan(user_id=user_id, context=context)
    except ValueError:
        log.error("study_plan: GEMINI_API_KEY not configured")
        return None
    except Exception:
        log.exception("study_plan: Gemini call failed for user=%.8s", user_id)
        return None


def run_weekly_summary_for_all() -> None:
    """Send weekly summaries to every user that has at least one push subscription."""
    from app.database.connection import get_connection
    from app.services.push_service import PushNotificationService

    push_svc = PushNotificationService()

    with get_connection() as db:
        user_ids = [
            r[0] for r in db.execute(
                "SELECT DISTINCT user_id FROM push_subscriptions"
            ).fetchall()
        ]

    for user_id in user_ids:
        try:
            result = generate_weekly_summary_for_user(user_id)
            if not result:
                continue

            summary_text = result.get("summary") or "Týdenní shrnutí je připraveno."
            body = summary_text[:120] + "…" if len(summary_text) > 120 else summary_text
            push_svc.send_to_user(user_id, "Bakix – Týdenní shrnutí", body)
            log.info("weekly_summary: push sent for user=%.8s", user_id)
        except Exception:
            log.exception("weekly_summary: run failed for user=%.8s", user_id)
