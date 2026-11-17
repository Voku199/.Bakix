"""„Sepsat zadání pro AI": z toho, co Bakix ví (předmět, téma, známka, třída, co se teď
probírá), složí hotové zadání, které si žák vloží do AI, kterou sám používá.

Skládá se šablonou, bez volání AI. Do zadání nejde jméno, škola ani nic dalšího, podle čeho
by šlo žáka poznat. Volitelná odpověď „hned tady" (services/ai_client.py) používá stejný text.
"""

import re
from dataclasses import dataclass
from urllib.parse import quote

from flask import current_app

from bakix.services.text import fold

GOALS = {
    "pochopit": "Pochopit látku od začátku",
    "oprava": "Připravit se na opravu nebo písemku",
    "procvicit": "Jen procvičit příklady",
}
DEFAULT_GOAL = "pochopit"
MAX_TOPIC = 120


@dataclass(frozen=True)
class StudyRequest:
    subject: str
    topic: str = ""
    mark: str = ""  # "5", "4-"…; prázdné = bez zmínky o známce
    class_name: str = ""  # "8.A"
    goal: str = DEFAULT_GOAL
    for_parent: bool = False
    current_topics: tuple[str, ...] = ()  # co se teď ve škole probírá


def clean(text: str, limit: int = MAX_TOPIC) -> str:
    """Jeden řádek bez řídicích znaků (vstup jde do zadání a do URL)."""
    return " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", text or "").split())[:limit]


def grade_label(class_name: str) -> str:
    """ "8.A" → "8. třídy". U tříd bez čísla (kvinta, 2.B SŠ nepoznáme) raději nic."""
    match = re.match(r"^\s*([1-9])\s*\.", class_name or "")
    return f"{match.group(1)}. třídy" if match else ""


def build_prompt(request: StudyRequest) -> str:
    subject = clean(request.subject) or "škola"
    topic = clean(request.topic)
    grade = grade_label(request.class_name)
    who = f"žák {grade}" if grade else "žák"
    about = f"z tématu „{topic}“" if topic else "z poslední látky"
    mark = clean(request.mark, 4)

    if request.for_parent:
        intro = f"Jsem rodič a moje dítě ({who}) má potíže v předmětu {subject}, konkrétně {about}."
        if mark:
            intro += f" Naposledy z toho dostalo známku {mark}."
        intro += " Chci mu pomoct, ale sám/sama si látku už moc nepamatuju."
        role = "Pomoz mi jako zkušený učitel, který radí rodičům."
        steps = [
            "Nejdřív mi látku stručně a srozumitelně připomeň, ať vím, o co jde.",
            "Poraď, jak ji dítěti vysvětlit: po malých krocích, na příkladech z běžného života.",
            "Připrav 5 příkladů od nejlehčího po těžší, které spolu můžeme projít, "
            "a ke každému správné řešení s postupem pro mě.",
            "Napiš, podle čeho poznám, že tomu dítě rozumí, a jaké chyby se dělají nejčastěji.",
        ]
    else:
        intro = f"Jsem {who}. V předmětu {subject} mi nejde látka {about}."
        if mark:
            intro += f" Naposledy jsem z toho dostal/a známku {mark}."
        intro += " Chci se to doopravdy naučit, ne jen opsat výsledek."
        role = "Buď můj trpělivý doučovatel."
        if request.goal == "procvicit":
            steps = [
                "Dávej mi příklady po jednom, od nejlehčího po těžší. Celkem 8.",
                "Řešení neprozrazuj. Počkej na moji odpověď a řekni, jestli je správně.",
                "Když udělám chybu, naveď mě nápovědou a nech mě to zkusit znovu.",
                "Na konci mi řekni, co mi šlo, co ne, a co si mám procvičit příště.",
            ]
        elif request.goal == "oprava":
            steps = [
                "Zeptej se mě, kdy písemku píšu a kolik času denně na učení mám.",
                "Polož mi 3 krátké otázky, abys zjistil, co už umím a kde dělám chyby. "
                "Počkej na moje odpovědi.",
                "Podle toho mi sestav plán učení po dnech až do písemky (bloky nejvýš 25 minut).",
                "Projdi se mnou první blok hned teď: vysvětli, dej příklad, nech mě počítat.",
                "Nakonec mi dej zkušební písemku na 10 minut a oprav mi ji.",
            ]
        else:
            steps = [
                "Nejdřív mi polož 3 krátké otázky, abys zjistil, co už umím a kde se ztrácím. "
                "Počkej na moje odpovědi.",
                "Vysvětli mi látku jednoduše a po malých krocích, na příkladech z běžného života. "
                "Po každém kroku se zeptej, jestli tomu rozumím.",
                "Dej mi 5 příkladů od nejlehčího po těžší. Řešení neprozrazuj: nech mě počítat, "
                "a když udělám chybu, naveď mě nápovědou.",
                "Na konci shrň, co si mám zapamatovat (max. 5 bodů), a řekni, co zopakovat zítra.",
            ]

    lines = [intro, "", role + " Postupuj takhle:"]
    lines += [f"{index}. {step}" for index, step in enumerate(steps, start=1)]

    current = [clean(t) for t in request.current_topics if clean(t)]
    if current:
        lines += ["", "Ve škole teď probíráme: " + "; ".join(dict.fromkeys(current[:5])) + "."]

    level = f"učiva {grade}" if grade else "učiva mojí třídy"
    lines += [
        "",
        f"Piš česky, stručně a přátelsky. Drž se {level} a nepoužívej nic, co jsme ještě nebrali. "
        "Když si nejsi jistý, co přesně jsme probírali, zeptej se mě.",
    ]
    return "\n".join(lines)


def build_inline_prompt(request: StudyRequest) -> str:
    """Zadání pro odpověď přímo v Bakixu: jedna zpráva, AI se nemůže doptávat."""
    subject = clean(request.subject) or "škola"
    topic = clean(request.topic) or "poslední probíraná látka"
    grade = grade_label(request.class_name)
    level = f"žáka {grade}" if grade else "žáka"
    current = "; ".join(dict.fromkeys(clean(t) for t in request.current_topics[:5] if clean(t)))
    parts = [
        f"Vysvětli téma „{topic}“ z předmětu {subject} pro {level}.",
        "Struktura odpovědi: 1) o co jde, dvěma větami; 2) vysvětlení po krocích s jedním "
        "příkladem z běžného života; 3) jeden vzorově vyřešený příklad; 4) tři příklady na "
        "procvičení bez řešení; 5) nejčastější chyba.",
    ]
    if current:
        parts.append(f"Ve škole se teď probírá: {current}.")
    return "\n".join(parts)


INLINE_SYSTEM = (
    "Jsi trpělivý doučovatel českých žáků základních a středních škol. Piš česky, jednoduše "
    "a krátce (nejvýš 350 slov). Používej nadpisy (##), odrážky a tučné písmo. Vzorce piš "
    "obyčejným textem (a² + b² = c²), nepoužívej LaTeX ani HTML. Neptej se na doplňující "
    "otázky a nepiš nic mimo učivo."
)


# --- kam zadání vložit ----------------------------------------------------------

# Otevření AI s předvyplněným zadáním. Zadání neobsahuje nic osobního; přesto jde do cizí
# služby až po kliknutí uživatele.
_ASSISTANTS = (
    ("ChatGPT", "https://chatgpt.com/?q={q}"),
    ("Claude", "https://claude.ai/new?q={q}"),
)


def assistant_links(prompt: str) -> list[tuple[str, str]]:
    encoded = quote(prompt, safe="")
    return [(name, template.format(q=encoded)) for name, template in _ASSISTANTS]


# --- propojení s Knowixem -------------------------------------------------------

_KNOWIX_TOPICS = (
    (("present perfect", "predpritomn"), "present-perfect", "Present perfect"),
    (("irregular", "nepravideln"), "nepravidelna-slovesa", "Nepravidelná slovesa"),
    (
        ("preposition", "predlozk", "at on in", "at, on, in"),
        "predlozky-at-on-in",
        "Předložky at / on / in",
    ),
)


@dataclass(frozen=True)
class KnowixTip:
    title: str
    url: str
    exact: bool  # True = vede přímo na cvičení k tématu


def is_english(subject: str) -> bool:
    name = fold(subject)
    return "angl" in name or name in ("aj", "anj", "english")


def knowix_tip(subject: str, topic: str = "") -> KnowixTip | None:
    """U angličtiny nabídne procvičení na Knowixu, když jde, tak přímo k tématu."""
    base = current_app.config.get("KNOWIX_URL")
    if not base or not is_english(subject):
        return None
    folded = fold(topic)
    for keywords, slug, title in _KNOWIX_TOPICS:
        if any(keyword in folded for keyword in keywords):
            return KnowixTip(title=title, url=f"{base}/cviceni/{slug}", exact=True)
    return KnowixTip(title="Angličtina hrou", url=f"{base}/cviceni", exact=False)
