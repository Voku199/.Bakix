"""Stránky se školními daty: přehled, známky, rozvrh, úkoly, zprávy, absence a učení.

Blueprint se registruje dvakrát (routes/__init__.py): jako "app" pro přihlášené a jako
"demo" na /ukazka s vymyšlenými daty. Odkazy uvnitř proto používají url_for(".něco").
"""

from datetime import date, timedelta

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from bakix.extensions import limiter
from bakix.services import accounts, ai_client, insights, richtext, study
from bakix.services.demo import DemoSource
from bakix.services.school import mark_value
from bakix.services.sources import LiveSource, SourceError
from bakix.timeutil import WEEKDAYS, monday_of, now, today

bp = Blueprint("school", __name__)

WEEK = timedelta(days=7)
MAX_WEEK_OFFSET = 12  # o kolik týdnů jde v rozvrhu listovat


@bp.before_request
def load_source():
    if request.blueprint == "demo":
        g.source = DemoSource()
        return None
    account = accounts.current()
    if account is None:
        return redirect(url_for("auth.login"))
    g.source = LiveSource(account)
    return None


@bp.context_processor
def inject_source():
    return {"source": g.get("source"), "profile": g.source.profile if "source" in g else None}


def _try(loader):
    """(data, None) nebo (None, hláška). Jedna nedostupná část nesmí shodit celou stránku."""
    try:
        return loader(), None
    except SourceError as error:
        return None, str(error)


# --- přehled --------------------------------------------------------------------


@bp.get("/prehled")
def overview():
    source = g.source
    moment = now()
    monday = monday_of(moment.date())
    source.preload("marks", f"week:{monday.isoformat()}", "homeworks", "messages")

    days, timetable_error = _try(lambda: source.week(monday))
    focus = insights.focus_day(days or [], moment)
    if focus is None and days is not None:
        # Pátek odpoledne nebo víkend: podívej se do příštího týdne.
        next_days, _ = _try(lambda: source.week(monday + WEEK))
        focus = insights.focus_day(next_days or [], moment)

    subjects, marks_error = _try(source.subjects)
    homeworks, homeworks_error = _try(source.homeworks)
    messages, messages_error = _try(source.messages)

    return render_template(
        "school/overview.html",
        moment=moment,
        focus=focus,
        states=insights.lesson_states(focus.day, moment) if focus else [],
        timetable_error=timetable_error,
        glance=insights.glance(focus, homeworks, subjects, messages, moment),
        marks=insights.recent_marks(subjects, 5) if subjects else [],
        marks_error=marks_error,
        attention=insights.attention(subjects, moment.date()) if subjects else [],
        homeworks=(homeworks or [])[:4],
        homeworks_total=len(homeworks or []),
        homeworks_error=homeworks_error,
        messages=(messages or [])[:3],
        unread=sum(1 for m in messages or [] if not m.read),
        messages_error=messages_error,
    )


# --- známky ---------------------------------------------------------------------


@bp.get("/znamky")
def marks():
    subjects, error = _try(g.source.subjects)
    return render_template(
        "school/marks.html",
        subjects=subjects or [],
        error=error,
        attention=insights.attention(subjects, today()) if subjects else [],
    )


@bp.get("/znamky/<subject_id>")
def subject(subject_id: str):
    subjects, error = _try(g.source.subjects)
    if error:
        return render_template("school/marks.html", subjects=[], error=error, attention=[])
    found = next((s for s in subjects if s.id == subject_id), None)
    if found is None:
        abort(404)

    # Kalkulačka „co když“: obyčejný GET formulář, bez JavaScriptu.
    value = mark_value(request.args.get("znamka", ""))
    weight = request.args.get("vaha", type=int)
    weight = weight if weight and 1 <= weight <= 10 else 1
    return render_template(
        "school/subject.html",
        subject=found,
        themes=g.source.themes(subject_id)[:8],
        what_if=insights.what_if(found, value, weight) if value else None,
        what_if_mark=request.args.get("znamka", "") if value else "",
        weight=weight,
        goal=insights.next_goal(found, weight),
        knowix=study.knowix_tip(found.name),
    )


# --- rozvrh ---------------------------------------------------------------------


@bp.get("/rozvrh")
def timetable():
    this_monday = monday_of(today())
    try:
        monday = monday_of(date.fromisoformat(request.args.get("tyden", "")))
    except ValueError:
        monday = this_monday
    if abs((monday - this_monday).days) > MAX_WEEK_OFFSET * 7:
        monday = this_monday
    days, error = _try(lambda: g.source.week(monday))
    moment = now()
    return render_template(
        "school/timetable.html",
        days=[d for d in days or [] if d.lessons or d.note],
        error=error,
        monday=monday,
        previous=monday - WEEK,
        following=monday + WEEK,
        is_current=monday == this_monday,
        today=moment.date(),
        weekdays=WEEKDAYS,
        states={d.day: insights.lesson_states(d, moment) for d in days or []},
    )


# --- úkoly, zprávy, absence -------------------------------------------------------


@bp.get("/ukoly")
def homeworks():
    items, error = _try(g.source.homeworks)
    return render_template(
        "school/homeworks.html", homeworks=items or [], error=error, today=today()
    )


@bp.get("/zpravy")
def messages():
    items, error = _try(g.source.messages)
    return render_template("school/messages.html", messages=(items or [])[:40], error=error)


@bp.get("/zpravy/<message_id>")
def message(message_id: str):
    items, error = _try(g.source.messages)
    if error:
        return render_template("school/messages.html", messages=[], error=error)
    found = next((m for m in items if m.id == message_id), None)
    if found is None:
        abort(404)
    return render_template("school/message.html", message=found)


@bp.get("/absence")
def absence():
    data, error = _try(g.source.absence)
    return render_template("school/absence.html", absence=data, error=error)


# --- učení: zadání pro AI ----------------------------------------------------------


def _study_request(values, subjects) -> tuple[study.StudyRequest, object | None]:
    """Sestaví požadavek z formuláře. Předmět se bere ze seznamu předmětů účtu, pokud je."""
    subject_id = values.get("predmet", "")[:40]
    found = next((s for s in subjects if s.id == subject_id), None)
    name = found.name if found else study.clean(values.get("predmet_text", ""), 60)
    topic = study.clean(values.get("tema", ""))
    goal = values.get("cil", study.DEFAULT_GOAL)
    profile = g.source.profile
    for_parent = values.get("pro", "rodic" if profile.is_parent else "ja") == "rodic"

    current: list[str] = []
    if found:
        current = [theme.text for theme in g.source.themes(found.id)[:4]]
        if not topic:
            latest = found.marks[0] if found.marks else None
            topic = (latest.topic if latest else "") or (current[0] if current else "")
    mark = values.get("znamka", "")[:4]
    return (
        study.StudyRequest(
            subject=name,
            topic=topic,
            mark=mark if mark_value(mark) else "",
            class_name=profile.class_name,
            goal=goal if goal in study.GOALS else study.DEFAULT_GOAL,
            for_parent=for_parent,
            current_topics=tuple(current),
        ),
        found,
    )


def _study_page(values, *, answer=None, answer_error=None):
    subjects, _ = _try(g.source.subjects)
    subjects = subjects or []
    request_, found = _study_request(values, subjects)
    ready = bool(request_.subject)
    prompt = study.build_prompt(request_) if ready else ""
    ai_on = ai_client.is_enabled() and not g.source.is_demo
    return render_template(
        "school/study.html",
        subjects=subjects,
        selected=found,
        req=request_,
        goals=study.GOALS,
        prompt=prompt,
        assistants=study.assistant_links(prompt) if ready else [],
        knowix=study.knowix_tip(request_.subject, request_.topic) if ready else None,
        attention=insights.attention(subjects, today()) if not ready else [],
        ai_enabled=ai_on,
        ai_remaining=ai_client.remaining_today(g.source.account) if ai_on else 0,
        answer=answer,
        answer_error=answer_error,
    )


@bp.get("/uceni")
def study_page():
    return _study_page(request.args)


@bp.post("/uceni/vysvetlit")
@limiter.limit("6 per minute")
def study_explain():
    """Volitelná odpověď AI přímo v Bakixu. Odchází jen předmět, téma a ročník."""
    if g.source.is_demo or not ai_client.is_enabled():
        abort(404)
    subjects, _ = _try(g.source.subjects)
    request_, _found = _study_request(request.form, subjects or [])
    if not request_.subject:
        flash("Vyber předmět.", "error")
        return redirect(url_for(".study_page"))
    try:
        reply = ai_client.ask(
            g.source.account,
            study.INLINE_SYSTEM,
            study.build_inline_prompt(request_),
            purpose="study",
        )
    except ai_client.AIError as error:
        return _study_page(request.form, answer_error=str(error))
    return _study_page(request.form, answer=richtext.render(reply.text))


# --- obnovení dat ------------------------------------------------------------------


@bp.post("/obnovit")
@limiter.limit("10 per minute")
def refresh():
    g.source.refresh()
    target = request.form.get("zpet", "")
    # Jen místní cesta: žádné //cizi.web ani https://…
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        target = url_for(".overview")
    return redirect(target)
