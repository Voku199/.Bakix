"""Odkud se berou školní data: `LiveSource` (Bakaláři přihlášeného účtu) a `DemoSource`
(ukázka, services/demo.py). Obě mají stejné metody, takže routy a šablony nerozlišují.

Každá metoda vrací hotové třídy ze services/school.py, nebo vyhodí SourceError s hláškou
pro uživatele. Stránka se pak vykreslí i s jednou nedostupnou částí.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from flask import current_app

from bakix.models import Account
from bakix.services import accounts, bakalari, cache, school
from bakix.services.bakalari import BakalariError, NotSupported, Unauthorized
from bakix.timeutil import monday_of, today

log = logging.getLogger(__name__)


class SourceError(Exception):
    """Část dat se nepodařilo načíst. Zpráva se ukáže uživateli."""


class LiveSource:
    is_demo = False

    def __init__(self, account: Account):
        self.account = account
        data = accounts.secrets_of(account)
        self.profile = accounts.profile_of(account)
        self._client = accounts.client_for(data["school_url"])
        self._ttl = current_app.config["SCHOOL_CACHE_SECONDS"]

    # --- co umí --------------------------------------------------------------

    def subjects(self) -> list[school.Subject]:
        return school.parse_marks(self._load("marks"))

    def week(self, monday: date) -> list[school.Day]:
        return school.parse_timetable(self._load(f"week:{monday.isoformat()}"))

    def homeworks(self) -> list[school.Homework]:
        return school.parse_homeworks(self._load("homeworks"))

    def messages(self) -> list[school.Message]:
        return school.parse_messages(self._load("messages"))

    def absence(self) -> school.Absence:
        return school.parse_absence(self._load("absence"))

    def themes(self, subject_id: str) -> list[school.Theme]:
        try:
            return school.parse_themes(self._load(f"themes:{subject_id}"))
        except SourceError:
            return []  # témata jsou jen doplněk; hodně škol je nevede

    def preload(self, *keys: str) -> None:
        """Načte víc částí najednou (paralelně), ať přehled nečeká na každou zvlášť."""
        missing = [key for key in keys if cache.get(self.account.id, key) is None]
        if len(missing) < 2:
            return
        token = accounts.access_token(self.account)
        with ThreadPoolExecutor(max_workers=len(missing)) as pool:
            results = pool.map(lambda key: self._try_fetch(key, token), missing)
        for key, payload in zip(missing, results, strict=True):
            if payload is not None:
                cache.put(self.account.id, key, payload, self._ttl)

    def refresh(self) -> None:
        cache.clear(self.account.id)

    # --- načítání ------------------------------------------------------------

    def _request(self, key: str, token: str) -> dict:
        if key == "marks":
            return self._client.get(bakalari.MARKS, token)
        if key.startswith("week:"):
            return self._client.get(bakalari.TIMETABLE, token, {"date": key.removeprefix("week:")})
        if key == "homeworks":
            since = today() - timedelta(days=14)
            return self._client.get(bakalari.HOMEWORKS, token, {"from": since.isoformat()})
        if key == "messages":
            return self._client.post(bakalari.MESSAGES, token)
        if key == "absence":
            return self._client.get(bakalari.ABSENCE, token)
        if key.startswith("themes:"):
            return self._client.get(bakalari.themes_path(key.removeprefix("themes:")), token)
        raise KeyError(key)

    def _try_fetch(self, key: str, token: str) -> dict | None:
        """Pro preload ve vlákně: žádná databáze ani session, chyba = None (zkusí se pak znovu)."""
        try:
            return self._request(key, token)
        except BakalariError:
            return None

    def _load(self, key: str) -> dict:
        cached = cache.get(self.account.id, key)
        if isinstance(cached, dict):
            return cached
        try:
            try:
                payload = self._request(key, accounts.access_token(self.account))
            except Unauthorized:
                token = accounts.access_token(self.account, force_refresh=True)
                payload = self._request(key, token)
        except Unauthorized:
            # Ani čerstvý token neprošel: škola účet nepustí, je potřeba nové přihlášení.
            accounts.logout()
            raise accounts.ReloginRequired from None
        except NotSupported as error:
            raise SourceError(str(error)) from None
        except BakalariError as error:
            raise SourceError(str(error)) from None
        cache.put(self.account.id, key, payload, self._ttl)
        return payload


def current_week() -> date:
    return monday_of(today())
