"""„Kouknu a vím": z rozvrhu, známek a úkolů skládá krátké shrnutí a tipy, na čem zapracovat.

Všechno jsou obyčejná pravidla, žádná AI. Výsledek je proto pokaždé stejný, okamžitý,
zadarmo a dá se otestovat.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import ceil

from bakix.services.school import Day, Homework, Mark, Message, Subject, weighted_average
from bakix.timeutil import WEEKDAYS_IN, format_date

RECENT_MARK_DAYS = 21
NEW_MARK_DAYS = 3
BAD_MARK = 4.0
WEAK_AVERAGE = 3.5


def plural(count: int, one: str, few: str, many: str) -> str:
    """1 hodina, 2 hodiny, 5 hodin."""
    if count == 1:
        return f"{count} {one}"
    if 2 <= count <= 4:
        return f"{count} {few}"
    return f"{count} {many}"


def at_time(value: time) -> str:
    """ "v 8:00", ale "ve 13:30" (ve dvě, ve tři, ve čtyři, ve dvanáct…, ve dvacet…)."""
    preposition = "ve" if value.hour in (2, 3, 4, 12, 13, 14) or value.hour >= 20 else "v"
    return f"{preposition} {value:%-H:%M}"


# --- den, který teď uživatele zajímá ------------------------------------------


@dataclass(frozen=True)
class FocusDay:
    day: Day
    label: str  # "Dnes", "Zítra", "V pondělí", "17. listopadu"
    is_today: bool


def focus_day(days: list[Day], now: datetime) -> FocusDay | None:
    """Dnešek, dokud se učí; jinak nejbližší další den s výukou."""
    today = now.date()
    for day in sorted(days, key=lambda d: d.day):
        if day.day < today or not day.taught:
            continue
        if day.day == today:
            if day.end and now.time() >= day.end:
                continue  # dnes už je po škole
            return FocusDay(day, "Dnes", True)
        delta = (day.day - today).days
        if delta == 1:
            label = "Zítra"
        elif delta < 7:
            label = WEEKDAYS_IN[day.day.weekday()].capitalize()
        else:
            label = format_date(day.day)
        return FocusDay(day, label, False)
    return None


def lesson_states(day: Day, now: datetime) -> list[str]:
    """Pro každou hodinu dne: "past", "now", nebo "" (teprve bude)."""
    states = []
    for lesson in day.lessons:
        if day.day != now.date() or not lesson.begin or not lesson.end:
            states.append("")
        elif now.time() >= lesson.end:
            states.append("past")
        elif now.time() >= lesson.begin:
            states.append("now")
        else:
            states.append("")
    return states


# --- shrnutí nahoře na přehledu -------------------------------------------------


@dataclass(frozen=True)
class Glance:
    title: str
    notes: tuple[str, ...]


def glance(
    focus: FocusDay | None,
    homeworks: list[Homework] | None,
    subjects: list[Subject] | None,
    messages: list[Message] | None,
    now: datetime,
) -> Glance:
    today = now.date()
    notes: list[str] = []

    if focus is None:
        title = "V nejbližších dnech není v rozvrhu žádná výuka."
    else:
        day = focus.day
        count = plural(len(day.taught), "hodina", "hodiny", "hodin")
        if focus.is_today and day.start and now.time() >= day.start and day.end:
            title = f"Dnes {count}, konec {at_time(day.end)}."
        elif day.start and day.end:
            title = f"{focus.label} {count}, od {day.start:%-H:%M} do {day.end:%-H:%M}."
        else:
            title = f"{focus.label} {count}."
        for lesson in day.changes[:2]:
            notes.append(f"Změna v rozvrhu: {lesson.subject} – {lesson.change.lower()}")
        if len(day.changes) > 2:
            notes.append(f"A další změny v rozvrhu ({len(day.changes) - 2}).")

    if homeworks:
        soon = [h for h in homeworks if h.due and today <= h.due <= today + timedelta(days=1)]
        late = [h for h in homeworks if h.due and h.due < today]
        if soon:
            notes.append(f"Do zítřka: {plural(len(soon), 'úkol', 'úkoly', 'úkolů')}.")
        if late:
            notes.append(f"Po termínu: {plural(len(late), 'úkol', 'úkoly', 'úkolů')}.")

    if subjects:
        fresh = [m for m in recent_marks(subjects, 20) if _age(m, today) <= NEW_MARK_DAYS]
        if len(fresh) == 1:
            notes.append(f"Nová známka: {fresh[0].text} – {fresh[0].subject}.")
        elif fresh:
            notes.append(f"{plural(len(fresh), 'nová známka', 'nové známky', 'nových známek')}.")

    if messages:
        unread = sum(1 for m in messages if not m.read)
        if unread:
            notes.append(
                f"{plural(unread, 'nepřečtená zpráva', 'nepřečtené zprávy', 'nepřečtených zpráv')}."
            )

    return Glance(title=title, notes=tuple(notes))


# --- známky -------------------------------------------------------------------


def _age(mark: Mark, today: date) -> int:
    return (today - mark.day).days if mark.day else 10_000


def recent_marks(subjects: list[Subject], limit: int = 6) -> list[Mark]:
    marks = [mark for subject in subjects for mark in subject.marks]
    marks.sort(key=lambda m: m.day or date.min, reverse=True)
    return marks[:limit]


@dataclass(frozen=True)
class Attention:
    """Jedna věc, na které stojí za to zapracovat."""

    subject: Subject
    title: str  # "5 z matematiky"
    detail: str  # "Písemka · Pythagorova věta"
    topic: str  # předvyplní se do zadání pro AI
    mark: Mark | None = None


def attention(subjects: list[Subject], today: date, limit: int = 3) -> list[Attention]:
    items: list[Attention] = []
    seen: set[str] = set()

    bad = [
        mark
        for mark in recent_marks(subjects, 200)
        if mark.value is not None
        and mark.value >= BAD_MARK
        and _age(mark, today) <= RECENT_MARK_DAYS
    ]
    bad.sort(key=lambda m: (-(m.value or 0), _age(m, today)))
    by_id = {subject.id: subject for subject in subjects}
    for mark in bad:
        if mark.subject_id in seen:
            continue
        seen.add(mark.subject_id)
        detail = " · ".join(part for part in (mark.caption, mark.theme) if part)
        items.append(
            Attention(
                subject=by_id[mark.subject_id],
                title=f"{mark.text} – {mark.subject}",
                detail=detail,
                topic=mark.topic,
                mark=mark,
            )
        )

    for subject in sorted(subjects, key=lambda s: -(s.average or 0)):
        if subject.id in seen:
            continue
        latest = subject.marks[0] if subject.marks else None
        if subject.average is not None and subject.average >= WEAK_AVERAGE:
            title = f"{subject.name}: průměr {subject.average_text}"
        elif subject.trend == "down":
            title = f"{subject.name}: poslední známky jsou horší"
        else:
            continue
        seen.add(subject.id)
        items.append(
            Attention(
                subject=subject,
                title=title,
                detail=latest.topic if latest else "",
                topic=latest.topic if latest else "",
                mark=None,
            )
        )
    return items[:limit]


# --- kalkulačka průměru ---------------------------------------------------------


def _weighted(subject: Subject) -> list[tuple[float, int]]:
    return [(m.value, m.weight or 1) for m in subject.marks if m.value is not None]


def what_if(subject: Subject, value: float, weight: int) -> float | None:
    """Průměr předmětu, kdyby přibyla známka `value` s vahou `weight`."""
    return weighted_average([*_weighted(subject), (value, weight)])


@dataclass(frozen=True)
class Goal:
    grade: int  # známka na vysvědčení, na kterou míříme
    limit: float  # průměr, pod který se musíme dostat
    ones_needed: int  # kolik jedniček dané váhy k tomu chybí


def next_goal(subject: Subject, weight: int = 1) -> Goal | None:
    """Kolik jedniček chybí k lepší známce na vysvědčení (orientačně: 1,49 / 2,49 / …)."""
    marks = _weighted(subject)
    average = weighted_average(marks)
    if average is None or average < 1.5:
        return None
    current = min(5, int(average + 0.5))
    grade = current - 1
    limit = grade + 0.49
    total_weight = sum(w for _, w in marks)
    total = sum(v * w for v, w in marks)
    # (total + n·w·1) / (total_weight + n·w) <= limit
    needed = (total - limit * total_weight) / (weight * (limit - 1))
    return Goal(grade=grade, limit=limit, ones_needed=max(1, ceil(needed - 1e-9)))
