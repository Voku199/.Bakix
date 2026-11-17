"""Klient pro Bakaláři API v3. Jen HTTP a chyby, žádná databáze ani session.

Pravidla:
- přihlášení heslem proběhne jednou; heslo se nikam neukládá, dál se používají tokeny,
- žádná přesměrování, jen https, veřejná IP (viz services/net.py),
- text odpovědi školy se nikdy neposílá uživateli ani do logu (může obsahovat cokoli).
"""

import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import quote

import requests

from bakix.services import net

log = logging.getLogger(__name__)

CLIENT_ID = "ANDR"
USER_AGENT = "Bakix/2 (+https://bakix.cz)"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class BakalariError(Exception):
    """Škola neodpověděla, jak má. Zpráva je bezpečná pro uživatele."""

    def __init__(self, message: str = "Školní server teď neodpovídá. Zkus to za chvíli."):
        super().__init__(message)


class InvalidCredentials(BakalariError):
    def __init__(self) -> None:
        super().__init__("Špatné jméno nebo heslo.")


class Unauthorized(BakalariError):
    """Token už neplatí (401). Volající má token obnovit, nebo uživatele přihlásit znovu."""


class NotSupported(BakalariError):
    """Škola tenhle modul nemá zapnutý (403/404/405)."""


@dataclass(frozen=True)
class Tokens:
    access: str
    refresh: str
    expires_at: float  # epoch (s)


class BakalariClient:
    def __init__(
        self, base_url: str, *, timeout: float = 10, session: requests.Session | None = None
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = session or requests.Session()

    # --- přihlášení ---------------------------------------------------------

    def login(self, username: str, password: str) -> Tokens:
        return self._token_request(
            {"grant_type": "password", "username": username, "password": password}
        )

    def refresh(self, refresh_token: str) -> Tokens:
        return self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})

    def _token_request(self, data: dict[str, str]) -> Tokens:
        response = self._send("POST", "/api/login", data={**data, "client_id": CLIENT_ID})
        if response.status_code in (400, 401):
            # Bakaláři vrací 400 invalid_grant na špatné heslo i na propadlý refresh token.
            if data["grant_type"] == "password":
                raise InvalidCredentials
            raise Unauthorized
        payload = self._json(response)
        access = payload.get("access_token")
        refresh = payload.get("refresh_token")
        if not isinstance(access, str) or not isinstance(refresh, str) or not access or not refresh:
            raise BakalariError
        expires_in = payload.get("expires_in")
        lifetime = expires_in if isinstance(expires_in, int) and expires_in > 0 else 3600
        return Tokens(access=access, refresh=refresh, expires_at=time.time() + lifetime)

    # --- data ---------------------------------------------------------------

    def get(self, path: str, access_token: str, params: dict[str, str] | None = None) -> dict:
        return self._data("GET", path, access_token, params)

    def post(self, path: str, access_token: str) -> dict:
        return self._data("POST", path, access_token, None)

    def _data(self, method: str, path: str, token: str, params: dict[str, str] | None) -> dict:
        response = self._send(
            method, path, params=params, headers={"Authorization": f"Bearer {token}"}
        )
        if response.status_code == 401:
            raise Unauthorized
        if response.status_code in (403, 404, 405):
            raise NotSupported("Tuhle část škola v Bakalářích nemá zapnutou.")
        return self._json(response)

    # --- HTTP ---------------------------------------------------------------

    def _send(self, method: str, path: str, **kwargs) -> requests.Response:
        url = self.base_url + path
        try:
            net.ensure_public_url(url)
        except net.UnsafeUrl:
            log.warning("Odmítnutá adresa školy (není veřejný https server)")
            raise BakalariError("Adresa školy není platná.") from None
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        headers.update(kwargs.pop("headers", {}))
        try:
            return self._session.request(
                method,
                url,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,  # přesměrování by obešlo kontrolu adresy
                stream=True,
                **kwargs,
            )
        except requests.RequestException as error:
            log.warning("Bakaláři: %s %s selhalo (%s)", method, path, type(error).__name__)
            raise BakalariError from None

    @staticmethod
    def _json(response: requests.Response) -> dict:
        if response.status_code != 200:
            log.warning("Bakaláři: HTTP %s", response.status_code)
            raise BakalariError
        try:
            body = response.raw.read(MAX_RESPONSE_BYTES + 1, decode_content=True)
        except Exception:  # noqa: BLE001  (urllib3 vyhazuje různé chyby čtení)
            raise BakalariError from None
        finally:
            response.close()
        if len(body) > MAX_RESPONSE_BYTES:
            raise BakalariError
        try:
            payload = json.loads(body)
        except ValueError:
            raise BakalariError from None
        if not isinstance(payload, dict):
            raise BakalariError
        return payload


# Cesty API na jednom místě.
USER = "/api/3/user"
MARKS = "/api/3/marks"
TIMETABLE = "/api/3/timetable/actual"
HOMEWORKS = "/api/3/homeworks"
MESSAGES = "/api/3/komens/messages/received"
ABSENCE = "/api/3/absence/student"


def themes_path(subject_id: str) -> str:
    return "/api/3/subjects/themes/" + quote(subject_id, safe="")
