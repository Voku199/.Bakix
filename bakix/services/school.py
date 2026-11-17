"""Školní data v podobě, se kterou pracují šablony: známky, rozvrh, úkoly, zprávy, absence.

Funkce `parse_*` převádějí syrové odpovědi Bakaláři API v3 na malé neměnné třídy. Počítají
s tím, že cokoli může chybět nebo mít nečekaný typ: jedna divná položka nesmí shodit stránku.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from html.parser import HTMLParser

from bakix.services.text import is_generic_caption
from bakix.timeutil import parse_date, parse_datetime

# --- třídy -------------------------------------------------------------------


@dataclass(frozen=True)
class Profile:
    name: str = ""
    class_name: str = ""
    school_name: str = ""
    user_type: str = "student"  # student | parents | …

    @property
    def is_parent(self) -> bool:
        return self.user_type.startswith("parent")


@dataclass(frozen=True)
class Mark:
    id: str
    subject_id: str
    subject: str
    text: str  # jak ji napsal učitel: "2-", "N", "8/10"
    value: float | None  # 1.0–5.0 u klasické známky, jinak None
    weight: int | None
    caption: str
    theme: str
    type_note: str
    day: date | None
    is_new: bool = False

    @property
    def level(self) -> int:
        """1–5 pro barvu známky, 0 = bez barvy (body, slovní hodnocení)."""
        if self.value is None:
            return 0
        return max(1, min(5, int(self.value + 0.5)))

    @property
    def topic(self) -> str:
        """Z čeho známka je: téma, jinak popisek (pokud něco říká; "Písemka" neříká nic)."""
        if self.theme:
            return self.theme
        return "" if is_generic_caption(self.caption) else self.caption


@dataclass(frozen=True)
class Subject:
    id: str
    name: str
    abbrev: str
    average_text: str
    average: float | None
    marks: tuple[Mark, ...] = ()
    trend: str = ""  # "up" = zlepšuje se, "down" = zhoršuje se, "" = drží se / málo známek

    @property
    def level(self) -> int:
        if self.average is None:
            return 0
        return max(1, min(5, int(self.average + 0.5)))


@dataclass(frozen=True)
class Lesson:
    hour: str
    begin: time | None
    end: time | None
    subject: str
    abbrev: str
    teacher: str
    room: str
    theme: str = ""
    change: str = ""  # popis změny ("Supluje…", "Odpadá")
    cancelled: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.change) or self.cancelled

    @property
    def time_range(self) -> str:
        if not self.begin or not self.end:
            return ""
        return f"{self.begin:%-H:%M}–{self.end:%-H:%M}"


@dataclass(frozen=True)
class Day:
    day: date
    lessons: tuple[Lesson, ...] = ()
    note: str = ""  # "Státní svátek" apod.

    @property
    def taught(self) -> tuple[Lesson, ...]:
        return tuple(lesson for lesson in self.lessons if not lesson.cancelled)

    @property
    def start(self) -> time | None:
        return next((lesson.begin for lesson in self.taught if lesson.begin), None)

    @property
    def end(self) -> time | None:
        return next((lesson.end for lesson in reversed(self.taught) if lesson.end), None)

    @property
    def changes(self) -> tuple[Lesson, ...]:
        return tuple(lesson for lesson in self.lessons if lesson.changed)


@dataclass(frozen=True)
class Homework:
    id: str
    subject: str
    content: str
    due: date | None
    assigned: date | None
    has_attachments: bool = False


@dataclass(frozen=True)
class Message:
    id: str
    title: str
    sender: str
    sent: datetime | None
    text: str
    read: bool = True
    attachments: int = 0


@dataclass(frozen=True)
class Theme:
    day: date | None
    text: str


@dataclass(frozen=True)
class SubjectAbsence:
    subject: str
    lessons: int
    missed: int

    @property
    def percent(self) -> float:
        return 100 * self.missed / self.lessons if self.lessons else 0.0


@dataclass(frozen=True)
class Absence:
    excused: int = 0
    unexcused: int = 0
    unsolved: int = 0
    late: int = 0
    threshold: float = 0.0  # % zameškaných hodin, od kterého škola řeší neklasifikaci
    subjects: tuple[SubjectAbsence, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return self.excused + self.unexcused + self.unsolved


# --- pomocné -----------------------------------------------------------------


def _text(value: object, limit: int = 2000) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


_MARK_RE = re.compile(r"^([1-5])(-?)$")


def mark_value(text: str) -> float | None:
    """ "2" → 2.0, "2-" → 2.5. Body, "N", "A" a slovní hodnocení → None."""
    match = _MARK_RE.match(text.strip())
    if not match:
        return None
    return int(match.group(1)) + (0.5 if match.group(2) else 0.0)


def parse_average(text: str) -> float | None:
    try:
        value = float(text.replace(",", ".").strip())
    except ValueError:
        return None
    return value if 1.0 <= value <= 5.0 else None


def weighted_average(marks: list[tuple[float, int]]) -> float | None:
    total = sum(weight for _, weight in marks)
    if total <= 0:
        return None
    return sum(value * weight for value, weight in marks) / total


def _trend(marks: list[Mark]) -> str:
    """Poslední dvě známky proti těm před nimi. Potřebuje aspoň čtyři číselné známky."""
    values = [m.value for m in sorted(marks, key=lambda m: m.day or date.min) if m.value]
    if len(values) < 4:
        return ""
    recent = sum(values[-2:]) / 2
    earlier = sum(values[:-2]) / len(values[:-2])
    if recent - earlier >= 0.5:
        return "down"
    if earlier - recent >= 0.5:
        return "up"
    return ""


class _TextExtractor(HTMLParser):
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in ("p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Zpráva z Komens jako čistý text. HTML od školy se nikdy nevykresluje."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001  (rozbité HTML: radši nic než chyba)
        return ""
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# --- parsery -----------------------------------------------------------------


def parse_profile(payload: dict) -> Profile:
    full = _text(payload.get("FullName"), 120)
    return Profile(
        name=full.split(",")[0].strip(),
        class_name=_text(_dict(payload.get("Class")).get("Abbrev"), 20),
        school_name=_text(payload.get("SchoolOrganizationName"), 200),
        user_type=_text(payload.get("UserType"), 20).lower() or "student",
    )


def parse_marks(payload: dict) -> list[Subject]:
    subjects = []
    for item in _list(payload.get("Subjects")):
        item = _dict(item)
        info = _dict(item.get("Subject"))
        name = _text(info.get("Name"), 120)
        subject_id = _text(info.get("Id"), 40)
        if not name or not subject_id:
            continue
        marks = []
        for raw in _list(item.get("Marks")):
            raw = _dict(raw)
            text = _text(raw.get("MarkText"), 20)
            if not text:
                continue
            is_points = bool(raw.get("IsPoints"))
            weight = raw.get("Weight")
            marks.append(
                Mark(
                    id=_text(raw.get("Id"), 60),
                    subject_id=subject_id,
                    subject=name,
                    text=text,
                    value=None if is_points else mark_value(text),
                    weight=weight if isinstance(weight, int) and weight > 0 else None,
                    caption=_text(raw.get("Caption"), 200),
                    theme=_text(raw.get("Theme"), 200),
                    type_note=_text(raw.get("TypeNote"), 80),
                    day=parse_date(raw.get("MarkDate")) or parse_date(raw.get("EditDate")),
                    is_new=bool(raw.get("IsNew")),
                )
            )
        marks.sort(key=lambda m: m.day or date.min, reverse=True)
        average_text = _text(item.get("AverageText"), 10)
        subjects.append(
            Subject(
                id=subject_id,
                name=name,
                abbrev=_text(info.get("Abbrev"), 12),
                average_text=average_text,
                average=parse_average(average_text),
                marks=tuple(marks),
                trend=_trend(marks),
            )
        )
    return subjects


_CANCELLED = {"canceled", "cancelled", "removed"}


def _clock(value: object) -> time | None:
    if not isinstance(value, str):
        return None
    try:
        hours, minutes = value.strip().split(":")[:2]
        return time(int(hours), int(minutes))
    except (ValueError, TypeError):
        return None


def parse_timetable(payload: dict) -> list[Day]:
    def lookup(key: str, value: str) -> dict[str, str]:
        return {
            _text(x.get("Id"), 40): _text(x.get(value), 120)
            for x in map(_dict, _list(payload.get(key)))
            if x.get("Id") is not None
        }

    names = lookup("Subjects", "Name")
    abbrevs = lookup("Subjects", "Abbrev")
    teachers = lookup("Teachers", "Name")
    rooms = lookup("Rooms", "Abbrev")
    hours = {
        x.get("Id"): (
            _text(x.get("Caption"), 10),
            _clock(x.get("BeginTime")),
            _clock(x.get("EndTime")),
        )
        for x in map(_dict, _list(payload.get("Hours")))
    }

    days = []
    for raw_day in map(_dict, _list(payload.get("Days"))):
        day = parse_date(raw_day.get("Date"))
        if not day:
            continue
        lessons = []
        for atom in map(_dict, _list(raw_day.get("Atoms"))):
            caption, begin, end = hours.get(atom.get("HourId"), ("", None, None))
            change = _dict(atom.get("Change"))
            change_type = _text(change.get("ChangeType"), 40).lower()
            subject_id = _text(atom.get("SubjectId"), 40)
            description = _text(change.get("Description"), 200)
            subject = names.get(subject_id, "")
            cancelled = change_type in _CANCELLED or (bool(change) and not subject)
            if not subject and not change:
                continue
            lessons.append(
                Lesson(
                    hour=caption,
                    begin=begin,
                    end=end,
                    subject=subject or description or _text(change.get("TypeName"), 80) or "Změna",
                    abbrev=abbrevs.get(subject_id, ""),
                    teacher=teachers.get(_text(atom.get("TeacherId"), 40), ""),
                    room=rooms.get(_text(atom.get("RoomId"), 40), ""),
                    theme=_text(atom.get("Theme"), 200),
                    change=(description or _text(change.get("TypeName"), 80) or "Změna")
                    if change
                    else "",
                    cancelled=cancelled,
                )
            )
        lessons.sort(key=lambda lesson: lesson.begin or time.min)
        note = _text(raw_day.get("DayDescription"), 120)
        days.append(Day(day=day, lessons=tuple(lessons), note=note))
    days.sort(key=lambda d: d.day)
    return days


def parse_homeworks(payload: dict) -> list[Homework]:
    homeworks = []
    for raw in map(_dict, _list(payload.get("Homeworks"))):
        if raw.get("Done") or raw.get("Closed"):
            continue
        content = _text(raw.get("Content"), 1500)
        if not content:
            continue
        homeworks.append(
            Homework(
                id=_text(raw.get("ID") or raw.get("Id"), 60),
                subject=_text(_dict(raw.get("Subject")).get("Name"), 120),
                content=content,
                due=parse_date(raw.get("DateEnd")),
                assigned=parse_date(raw.get("DateStart") or raw.get("DateAward")),
                has_attachments=bool(_list(raw.get("Attachments"))),
            )
        )
    homeworks.sort(key=lambda h: h.due or date.max)
    return homeworks


def parse_messages(payload: dict) -> list[Message]:
    messages = []
    for raw in map(_dict, _list(payload.get("Messages"))):
        message_id = _text(raw.get("Id"), 80)
        if not message_id:
            continue
        messages.append(
            Message(
                id=message_id,
                title=_text(raw.get("Title"), 200) or "Zpráva",
                sender=_text(_dict(raw.get("Sender")).get("Name"), 120),
                sent=parse_datetime(raw.get("SentDate")),
                text=html_to_text(_text(raw.get("Text"), 20000)),
                read=bool(raw.get("Read")),
                attachments=len(_list(raw.get("Attachments"))),
            )
        )
    messages.sort(key=lambda m: m.sent.timestamp() if m.sent else 0, reverse=True)
    return messages


def parse_themes(payload: dict) -> list[Theme]:
    themes = []
    for raw in map(_dict, _list(payload.get("Themes"))):
        text = _text(raw.get("Theme") or raw.get("Title") or raw.get("Name"), 200)
        if text:
            themes.append(Theme(day=parse_date(raw.get("Date")), text=text))
    themes.sort(key=lambda t: t.day or date.min, reverse=True)
    return themes


def parse_absence(payload: dict) -> Absence:
    days = [_dict(x) for x in _list(payload.get("Absences"))]
    subjects = []
    for raw in map(_dict, _list(payload.get("AbsencesPerSubject"))):
        name = _text(raw.get("SubjectName"), 120)
        if name:
            subjects.append(
                SubjectAbsence(
                    subject=name,
                    lessons=_int(raw.get("LessonsCount")),
                    missed=_int(raw.get("Base")),
                )
            )
    subjects.sort(key=lambda s: s.percent, reverse=True)
    threshold = payload.get("PercentageThreshold")
    return Absence(
        excused=sum(_int(d.get("Ok")) for d in days),
        unexcused=sum(_int(d.get("Missed")) for d in days),
        unsolved=sum(_int(d.get("Unsolved")) for d in days),
        late=sum(_int(d.get("Late")) for d in days),
        threshold=float(threshold) * 100 if isinstance(threshold, (int, float)) else 0.0,
        subjects=tuple(subjects),
    )
