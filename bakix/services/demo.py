"""Ukázka: vymyšlená žákyně 8.A. Data se počítají od dnešního dne, takže ukázka nikdy nezestárne.

Stejné rozhraní jako LiveSource (services/sources.py). Ukázka nepotřebuje přihlášení, session
ani databázi; běží na adresách /ukazka/….
"""

from datetime import date, datetime, time, timedelta

from bakix.services.school import (
    Absence,
    Day,
    Homework,
    Lesson,
    Mark,
    Message,
    Profile,
    Subject,
    SubjectAbsence,
    Theme,
    _trend,
    weighted_average,
)
from bakix.timeutil import PRAGUE, monday_of, today

PROFILE = Profile(
    name="Tereza Nováková", class_name="8.A", school_name="ZŠ Ukázková", user_type="student"
)

_HOURS = [
    ("1", time(8, 0), time(8, 45)),
    ("2", time(8, 55), time(9, 40)),
    ("3", time(10, 0), time(10, 45)),
    ("4", time(10, 55), time(11, 40)),
    ("5", time(11, 50), time(12, 35)),
    ("6", time(12, 45), time(13, 30)),
]

# id: (název, zkratka, učitel, učebna)
_SUBJECTS = {
    "M": ("Matematika", "M", "Mgr. Eva Procházková", "203"),
    "CJ": ("Český jazyk", "ČJ", "Mgr. Jana Nováková", "101"),
    "AJ": ("Anglický jazyk", "AJ", "Mgr. Robert Smith", "105"),
    "F": ("Fyzika", "F", "Mgr. Václav Horáček", "Lab1"),
    "D": ("Dějepis", "D", "Mgr. Petr Dvořák", "102"),
    "PR": ("Přírodopis", "Př", "Mgr. Lenka Marková", "Lab2"),
    "INF": ("Informatika", "Inf", "Ing. Martin Blažek", "PC2"),
    "TV": ("Tělesná výchova", "TV", "Mgr. Pavel Krejčí", "Těl."),
    "Z": ("Zeměpis", "Z", "Mgr. Ivana Součková", "104"),
}

# pondělí … pátek
_WEEK = [
    ["M", "CJ", "AJ", "F", "INF", "TV"],
    ["CJ", "M", "D", "AJ", "PR"],
    ["M", "F", "CJ", "Z", "AJ", "TV"],
    ["AJ", "M", "PR", "CJ", "D"],
    ["CJ", "M", "Z", "INF"],
]

_THEMES = {
    "M": [
        "Pythagorova věta – výpočet přepony",
        "Pythagorova věta – slovní úlohy",
        "Druhá odmocnina",
    ],
    "CJ": ["Vedlejší věty – druhy", "Rozbor souvětí", "Karel Čapek: Bílá nemoc"],
    "AJ": ["Present perfect vs. past simple", "Irregular verbs – revision", "Unit 3: Travelling"],
    "F": ["Tlak v kapalinách", "Archimédův zákon", "Hustota látek"],
    "D": ["Napoleonské války", "Vídeňský kongres", "Průmyslová revoluce"],
    "PR": ["Trávicí soustava", "Dýchací soustava", "Oběhová soustava"],
    "INF": ["Tabulkový procesor – vzorce", "Grafy v tabulkách"],
    "Z": ["Jižní Amerika – povrch", "Jižní Amerika – státy"],
    "TV": [],
}

# (předmět, známka, váha, popisek, téma, před kolika školními dny)
_MARKS = [
    ("M", "5", 3, "Písemka", "Pythagorova věta", 1),
    ("M", "3", 2, "Desetiminutovka", "Druhá odmocnina", 6),
    ("M", "2", 2, "Písemka", "Mocniny s přirozeným mocnitelem", 14),
    ("M", "2", 1, "Aktivita v hodině", "", 19),
    ("M", "1", 1, "Domácí úkol", "Výrazy", 27),
    ("AJ", "4", 2, "Test", "Present perfect", 3),
    ("AJ", "2", 1, "Slovíčka", "Unit 3: Travelling", 9),
    ("AJ", "2-", 2, "Test", "Irregular verbs", 17),
    ("AJ", "1", 1, "Speaking", "My last holiday", 24),
    ("CJ", "1", 2, "Diktát", "Vyjmenovaná slova a shoda", 2),
    ("CJ", "2", 3, "Slohová práce", "Líčení", 11),
    ("CJ", "1", 1, "Čtenářský deník", "", 21),
    ("F", "2", 2, "Test", "Hustota látek", 4),
    ("F", "3", 1, "Laboratorní práce", "Měření objemu", 15),
    ("F", "2", 2, "Test", "Síla a její měření", 26),
    ("D", "1", 1, "Referát", "Napoleon Bonaparte", 5),
    ("D", "2", 2, "Test", "Velká francouzská revoluce", 18),
    ("PR", "2", 2, "Test", "Kosterní soustava", 8),
    ("PR", "1", 1, "Poznávačka", "Svaly", 22),
    ("INF", "1", 2, "Projekt", "Rozpočet třídního výletu", 7),
    ("Z", "2", 1, "Slepá mapa", "Severní Amerika", 12),
]


def _school_days_back(count: int, base: date) -> date:
    day = base
    while count > 0:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            count -= 1
    return day


def _school_days_ahead(count: int, base: date) -> date:
    day = base
    while count > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            count -= 1
    return day


class DemoSource:
    is_demo = True
    profile = PROFILE

    def __init__(self, base: date | None = None):
        self.today = base or today()

    def subjects(self) -> list[Subject]:
        subjects = []
        for subject_id, (name, abbrev, _, _) in _SUBJECTS.items():
            marks = [
                Mark(
                    id=f"demo-{subject_id}-{index}",
                    subject_id=subject_id,
                    subject=name,
                    text=text,
                    value=float(text[0]) + (0.5 if text.endswith("-") else 0),
                    weight=weight,
                    caption=caption,
                    theme=theme,
                    type_note="",
                    day=_school_days_back(ago, self.today),
                    is_new=ago <= 3,
                )
                for index, (sid, text, weight, caption, theme, ago) in enumerate(_MARKS)
                if sid == subject_id
            ]
            if not marks:
                continue
            average = weighted_average([(m.value, m.weight or 1) for m in marks if m.value])
            subjects.append(
                Subject(
                    id=subject_id,
                    name=name,
                    abbrev=abbrev,
                    average_text=f"{average:.2f}".replace(".", ","),
                    average=average,
                    marks=tuple(marks),
                    trend=_trend(marks),
                )
            )
        return subjects

    def week(self, monday: date) -> list[Day]:
        this_monday = monday_of(self.today)
        days = []
        for offset, plan in enumerate(_WEEK):
            day = monday + timedelta(days=offset)
            lessons = []
            for index, subject_id in enumerate(plan):
                name, abbrev, teacher, room = _SUBJECTS[subject_id]
                hour, begin, end = _HOURS[index]
                change, cancelled, theme = "", False, ""
                if day < self.today and _THEMES[subject_id]:
                    theme = _THEMES[subject_id][(offset + index) % len(_THEMES[subject_id])]
                # Ukázkové změny: dnes (nebo v pondělí, když je víkend) supluje fyzika a odpadá
                # poslední hodina.
                show_changes_on = self.today if self.today.weekday() < 5 else this_monday
                if day == show_changes_on and monday == monday_of(show_changes_on):
                    if index == 3:
                        change = "Supluje Mgr. Horáček, učebna 204"
                    elif index == len(plan) - 1 and len(plan) > 4:
                        change, cancelled = "Odpadá", True
                lessons.append(
                    Lesson(
                        hour=hour,
                        begin=begin,
                        end=end,
                        subject=name,
                        abbrev=abbrev,
                        teacher=teacher,
                        room=room,
                        theme=theme,
                        change=change,
                        cancelled=cancelled,
                    )  # fmt: skip
                )
            days.append(Day(day=day, lessons=tuple(lessons)))
        return days

    def homeworks(self) -> list[Homework]:
        base = self.today
        return [
            Homework(
                id="demo-hw-1",
                subject="Matematika",
                content="Pracovní sešit str. 34, cvičení 2–5 (Pythagorova věta – slovní úlohy).",
                due=_school_days_ahead(1, base),
                assigned=_school_days_back(1, base),
            ),
            Homework(
                id="demo-hw-2",
                subject="Anglický jazyk",
                content="Workbook p. 27, ex. 3 and 4. Naučit se nepravidelná slovesa (3. sloupec).",
                due=_school_days_ahead(3, base),
                assigned=_school_days_back(2, base),
            ),
            Homework(
                id="demo-hw-3",
                subject="Český jazyk",
                content="Napsat líčení na téma Podzimní ráno (aspoň 150 slov).",
                due=_school_days_ahead(6, base),
                assigned=_school_days_back(1, base),
                has_attachments=True,
            ),
        ]

    def messages(self) -> list[Message]:
        def sent(days_ago: int, hour: int) -> datetime:
            day = _school_days_back(days_ago, self.today)
            return datetime.combine(day, time(hour, 15), tzinfo=PRAGUE)

        return [
            Message(
                id="demo-1",
                title="Opravná písemka z matematiky",
                sender="Mgr. Eva Procházková",
                sent=sent(1, 14),
                text=(
                    "Dobrý den,\nopravná písemka na Pythagorovu větu bude ve čtvrtek 2. hodinu. "
                    "Kdo si chce známku opravit, ať se mi přihlásí do pondělí.\n\nE. Procházková"
                ),
                read=False,
            ),
            Message(
                id="demo-2",
                title="Třídní schůzky",
                sender="Mgr. Jana Nováková",
                sent=sent(4, 10),
                text=(
                    "Vážení rodiče,\nzveme vás na třídní schůzky, které proběhnou příští úterý "
                    "od 17:00 v učebně 101.\n\nS pozdravem\nJ. Nováková"
                ),
            ),
            Message(
                id="demo-3",
                title="Exkurze do planetária",
                sender="Mgr. Václav Horáček",
                sent=sent(8, 8),
                text="Exkurze se koná v pátek. Sraz v 7:45 před školou, s sebou 120 Kč a svačinu.",
                attachments=1,
            ),
        ]

    def absence(self) -> Absence:
        return Absence(
            excused=14,
            unexcused=0,
            unsolved=2,
            late=1,
            threshold=25.0,
            subjects=(
                SubjectAbsence("Tělesná výchova", 22, 4),
                SubjectAbsence("Matematika", 46, 4),
                SubjectAbsence("Český jazyk", 44, 3),
                SubjectAbsence("Anglický jazyk", 34, 2),
                SubjectAbsence("Fyzika", 22, 2),
                SubjectAbsence("Dějepis", 21, 1),
            ),
        )

    def themes(self, subject_id: str) -> list[Theme]:
        texts = _THEMES.get(subject_id, [])
        return [
            Theme(day=_school_days_back(1 + index * 2, self.today), text=text)
            for index, text in enumerate(texts)
        ]

    def preload(self, *keys: str) -> None:
        return None

    def refresh(self) -> None:
        return None
