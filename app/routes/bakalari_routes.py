import json
import logging

from flask import Blueprint, render_template, session, redirect, url_for

from app.database.db import fetch_row
from app.services.bakalari import BakalariService

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


@bakalari_bp.route("/")
def index():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.welcome"))

    row = fetch_row(user_id)
    if not row:
        log.warning("index: no DB row for user=%.8s, clearing session", user_id)
        session.clear()
        return redirect(url_for("auth.welcome"))

    svc   = BakalariService(base_url=row["school_url"])
    token = svc.get_token(user_id)
    if not token:
        session.clear()
        return redirect(url_for("auth.welcome"))

    marks_data = svc.get_marks(token)

    if marks_data.get("status_code") == 401:
        # Token expired — attempt re-authentication from stored credentials
        log.info("index: token expired for user=%.8s, reauthenticating", user_id)
        token = svc.reauth(user_id)
        if not token:
            session.clear()
            return redirect(url_for("auth.welcome"))
        marks_data = svc.get_marks(token)
        if marks_data.get("status_code") == 401:
            log.warning("index: reauth still returned 401 for user=%.8s", user_id)
            session.clear()
            return redirect(url_for("auth.welcome"))

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

    return render_template(
        "index.html",
        error=None,
        subjects=subjects,
        marks_error=marks_error,
        substitutions=substitutions,
        subs_error=subs_error,
        chart_data_json=json.dumps(chart_datasets, ensure_ascii=False),
    )
