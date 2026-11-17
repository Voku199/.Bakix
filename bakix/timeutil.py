"""Čas. "Dnes" je pro Bakix pražský den, ne UTC: rozvrh i termíny úkolů jsou v českém čase."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

PRAGUE = ZoneInfo("Europe/Prague")

WEEKDAYS = ("pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle")
WEEKDAYS_IN = ("v pondělí", "v úterý", "ve středu", "ve čtvrtek", "v pátek", "v sobotu", "v neděli")
MONTHS_GEN = (
    "ledna", "února", "března", "dubna", "května", "června",
    "července", "srpna", "září", "října", "listopadu", "prosince",
)  # fmt: skip


def utcnow() -> datetime:
    """Naivní UTC čas pro sloupce v databázi."""
    return datetime.now(UTC).replace(tzinfo=None)


def now() -> datetime:
    """Aktuální pražský čas (s časovou zónou)."""
    return datetime.now(PRAGUE)


def today() -> date:
    return now().date()


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def parse_datetime(value: object) -> datetime | None:
    """ISO datum/čas z Bakalářů ("2026-11-17T08:00:00+01:00") jako pražský čas."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=PRAGUE)
    return parsed.astimezone(PRAGUE)


def parse_date(value: object) -> date | None:
    parsed = parse_datetime(value)
    return parsed.date() if parsed else None


def format_date(day: date, *, weekday: bool = False, year: bool = False) -> str:
    """17. listopadu, případně "úterý 17. listopadu 2026"."""
    text = f"{day.day}. {MONTHS_GEN[day.month - 1]}"
    if year:
        text += f" {day.year}"
    if weekday:
        text = f"{WEEKDAYS[day.weekday()]} {text}"
    return text


def relative_day(day: date, base: date | None = None) -> str:
    """dnes / zítra / včera / v pátek / 3. prosince. Pro termíny úkolů a data známek."""
    base = base or today()
    delta = (day - base).days
    if delta == 0:
        return "dnes"
    if delta == 1:
        return "zítra"
    if delta == -1:
        return "včera"
    if 1 < delta < 7:
        return WEEKDAYS_IN[day.weekday()]
    if -7 < delta < -1:
        return f"před {-delta} dny"
    return format_date(day, year=day.year != base.year)
