"""Seznam škol z oficiálního adresáře Bakalářů (sluzby.bakalari.cz).

Slouží ke dvěma věcem:
  1. vyhledávání školy na přihlašovací stránce,
  2. jako allowlist: `resolve()` vrátí školu jen tehdy, když ji adresář (nebo
     BAKALARI_EXTRA_SCHOOLS) opravdu obsahuje. Na jinou adresu Bakix požadavek nepošle.

Seznam obcí i škol v obci se drží 24 h v paměti procesu.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from urllib.parse import quote

import requests
from flask import current_app

from bakix.services import net
from bakix.services.text import fold

log = logging.getLogger(__name__)

TTL = 24 * 60 * 60
MAX_CITIES = 8
MAX_RESULTS = 30
_HEADERS = {"Accept": "application/json", "User-Agent": "Bakix/2 (+https://bakix.cz)"}

_lock = threading.Lock()
_cities: tuple[float, list[str]] | None = None
_by_city: dict[str, tuple[float, list["School"]]] = {}


@dataclass(frozen=True)
class School:
    name: str
    city: str
    url: str


def search(query: str) -> list[School]:
    """Školy podle města a/nebo názvu ("brno gymnázium"). Prázdný seznam pro krátký dotaz."""
    tokens = [token for token in fold(query).split() if len(token) >= 2]
    if not tokens:
        return []

    found = [s for s in _extra() if all(t in fold(f"{s.city} {s.name}") for t in tokens)]

    cities = _municipalities()
    ranked: list[tuple[int, str]] = []
    for city in cities:
        folded = fold(city)
        hits = [t for t in tokens if t in folded]
        if hits:
            # Přesná shoda a začátek názvu obce mají přednost před shodou uprostřed.
            score = 0 if folded in tokens else 1 if any(folded.startswith(t) for t in hits) else 2
            ranked.append((score, city))
    ranked.sort()
    matched = [city for _, city in ranked[:MAX_CITIES]]

    if matched:
        # Vlákna z poolu nemají kontext aplikace, adresu adresáře proto dostanou hotovou.
        load = partial(_schools_in, base=current_app.config["SCHOOL_DIRECTORY_URL"])
        with ThreadPoolExecutor(max_workers=len(matched)) as pool:
            for schools in pool.map(load, matched):
                found.extend(
                    s for s in schools if all(t in fold(f"{s.city} {s.name}") for t in tokens)
                )
    # Stejná škola může být v adresáři pod víc obcemi (Brno a Brno-Bystrc).
    unique = list({school.url: school for school in reversed(found)}.values())[::-1]
    return unique[:MAX_RESULTS]


def resolve(city: str, url: str) -> School | None:
    """Škola z adresáře se zadaným městem a adresou, jinak None."""
    try:
        wanted = net.normalize_school_url(url)
    except net.UnsafeUrl:
        return None
    candidates = list(_extra())
    if city and city in _municipalities():
        candidates.extend(_schools_in(city))
    for school in candidates:
        if school.url == wanted and school.city == city:
            return school
    return None


def reset_cache() -> None:
    global _cities
    with _lock:
        _cities = None
        _by_city.clear()


# --- zdroje ------------------------------------------------------------------


def _extra() -> list[School]:
    schools = []
    for entry in current_app.config["BAKALARI_EXTRA_SCHOOLS"]:
        parts = [part.strip() for part in entry.split("|")]
        if len(parts) != 3:
            log.warning("BAKALARI_EXTRA_SCHOOLS: položka nemá tvar Název|Město|URL")
            continue
        try:
            schools.append(School(parts[0], parts[1], net.normalize_school_url(parts[2])))
        except net.UnsafeUrl:
            log.warning("BAKALARI_EXTRA_SCHOOLS: neplatná adresa školy %r", parts[0])
    return schools


def _fetch(url: str):
    response = requests.get(url, headers=_HEADERS, timeout=8, allow_redirects=False)
    response.raise_for_status()
    return response.json()


def _municipalities() -> list[str]:
    global _cities
    now = time.monotonic()
    with _lock:
        if _cities and _cities[0] > now:
            return _cities[1]
    try:
        payload = _fetch(current_app.config["SCHOOL_DIRECTORY_URL"])
        names = [m["name"] for m in payload if isinstance(m, dict) and m.get("name")]
    except (requests.RequestException, ValueError, TypeError, KeyError):
        log.warning("Seznam obcí se nepodařilo načíst")
        with _lock:
            return _cities[1] if _cities else []
    with _lock:
        _cities = (now + TTL, names)
    return names


def _schools_in(city: str, base: str | None = None) -> list[School]:
    now = time.monotonic()
    with _lock:
        cached = _by_city.get(city)
        if cached and cached[0] > now:
            return cached[1]
    base = base or current_app.config["SCHOOL_DIRECTORY_URL"]
    schools: list[School] = []
    try:
        payload = _fetch(f"{base}/{quote(city, safe='')}")
        for item in payload.get("schools", []):
            try:
                url = net.normalize_school_url(item["schoolUrl"])
            except (net.UnsafeUrl, KeyError, TypeError):
                continue
            if item.get("name"):
                schools.append(School(name=str(item["name"]).strip(), city=city, url=url))
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        log.warning("Školy v obci se nepodařilo načíst")
        return cached[1] if cached else []
    schools.sort(key=lambda s: fold(s.name))
    with _lock:
        _by_city[city] = (now + TTL, schools)
    return schools
