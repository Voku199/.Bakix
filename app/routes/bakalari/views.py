"""Full-page views: dashboard index, Bakix Wrap and the weekly summary."""

import datetime
import json
import logging
import os

from flask import render_template, session, redirect, url_for, jsonify
from flask_babel import gettext as _

from app.database.db import fetch_row
from app.extensions import limiter
from app.routes.bakalari import bakalari_bp
from app.routes.bakalari.helpers import (
    _build_chart_datasets, _holiday_info, get_user_projects,
)
from app.services.bakalari import BakalariService
from app.services import demo_data as _demo
from app.services.wrap_service import generate_wrap_for_user

log = logging.getLogger(__name__)

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
        return jsonify({"error": _("Interní chyba serveru")}), 500


@bakalari_bp.route("/shrnutí", methods=["GET", "POST"])
def api_shrnuti():
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Not authenticated"}), 401

        from app.services.weekly_summary import generate_weekly_summary_for_user
        result = generate_weekly_summary_for_user(user_id)
        if result is None:
            return jsonify({"error": _("Shrnutí se nepodařilo vygenerovat")}), 503

        return jsonify(result)
    except Exception:
        log.exception("api_shrnuti: unexpected error")
        return jsonify({"error": _("Interní chyba serveru")}), 500


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
