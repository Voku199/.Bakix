import logging
import os
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)


class BakalariService:

    _LOGIN     = "/api/login"
    _MARKS     = "/api/3/marks"
    _TIMETABLE = "/api/3/timetable/actual"
    _HOMEWORKS = "/api/3/homeworks"
    _KOMENS    = "/api/3/komens/messages/received"
    _THEMES    = "/api/3/subjects/themes/{subject_id}"

    def __init__(self, base_url: str = ""):
        self._base = (base_url or os.getenv("BAKALARI_URL", "")).rstrip("/")

    # ── School validation ────────────────────────────────────────────────────

    @staticmethod
    def validate_school_url(base_url: str) -> bool:
        """Return True if base_url serves a Bakaláře API (contains ApiVersion)."""
        base = base_url.rstrip("/")
        for path in ("/api/3", "/api"):
            try:
                r = requests.get(f"{base}{path}", timeout=6)
                data = r.json()
                if isinstance(data, dict) and "ApiVersion" in data:
                    return True
            except Exception:
                continue
        return False

    # ── Token management ─────────────────────────────────────────────────────

    def get_token(self, user_id: str) -> "str | None":
        """Return the stored access token for user_id, or attempt a fresh login if absent."""
        from app.database.db import fetch_row

        row = fetch_row(user_id)
        if not row:
            log.warning("get_token: no DB row for user=%.8s", user_id)
            return None

        token = row.get("access_token")
        if token:
            log.debug("get_token: serving stored token for user=%.8s", user_id)
            return token

        log.info("get_token: no stored token, reauthenticating user=%.8s", user_id)
        return self._reauth_from_db(user_id)

    def reauth(self, user_id: str) -> "str | None":
        """Force a fresh login from stored encrypted credentials and persist new tokens."""
        log.info("reauth: forced reauth for user=%.8s", user_id)
        return self._reauth_from_db(user_id)

    def _reauth_from_db(self, user_id: str) -> "str | None":
        from app.database.db import fetch_row, update_tokens
        from app.services.crypto import decrypt_json

        row = fetch_row(user_id)
        if not row:
            return None

        try:
            creds = decrypt_json(row["enc_creds"])
        except Exception:
            log.error("_reauth_from_db: credential decryption failed for user=%.8s", user_id)
            return None

        result = self.login(creds["username"], creds["password"])
        if "error" in result:
            log.warning("_reauth_from_db: login failed for user=%.8s: %s", user_id, result.get("error"))
            return None

        update_tokens(user_id, result["access_token"], result.get("refresh_token"))
        log.info("_reauth_from_db: tokens refreshed for user=%.8s", user_id)
        return result["access_token"]

    # ── Auth & API ───────────────────────────────────────────────────────────

    def login(self, username: str, password: str) -> dict:
        try:
            response = requests.post(
                f"{self._base}{self._LOGIN}",
                data={
                    "grant_type": "password",
                    "username":   username,
                    "password":   password,
                    "client_id":  "ANDR",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            )
        except requests.RequestException as exc:
            return {"error": "Login request failed", "detail": str(exc)}

        if not response.ok:
            return {
                "error":       "Login failed",
                "status_code": response.status_code,
                "detail":      response.text,
            }
        data = response.json()
        return {
            "access_token":  data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
        }

    def get_marks(self, access_token: str) -> dict:
        try:
            response = requests.get(
                f"{self._base}{self._MARKS}",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
        except requests.RequestException:
            log.exception("get_marks: request failed")
            return {"error": "Marks request failed"}
        if not response.ok:
            return {"error": "Failed to fetch marks", "status_code": response.status_code}
        try:
            return response.json()
        except ValueError:
            log.exception("get_marks: non-JSON response (HTTP %s)", response.status_code)
            return {"error": "Invalid JSON response", "status_code": response.status_code}

    def get_timetable(self, access_token: str) -> dict:
        try:
            response = requests.get(
                f"{self._base}{self._TIMETABLE}",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
        except requests.RequestException:
            log.exception("get_timetable: request failed")
            return {"error": "Timetable request failed"}
        if not response.ok:
            return {"error": "Failed to fetch timetable", "status_code": response.status_code}
        try:
            return response.json()
        except ValueError:
            log.exception("get_timetable: non-JSON response (HTTP %s)", response.status_code)
            return {"error": "Invalid JSON response", "status_code": response.status_code}

    def get_substitutions_from_timetable(self, access_token: str) -> list:
        timetable = self.get_timetable(access_token)
        if "error" in timetable:
            return timetable

        changes = []
        for day in timetable.get("Days", []):
            for atom in day.get("Atoms", []):
                if atom.get("Change") is not None:
                    changes.append({
                        "day":    day.get("Date"),
                        "hour":   atom.get("HourId"),
                        "change": atom.get("Change"),
                    })
        return changes

    def get_homeworks(self, access_token: str, from_date: str, to_date: str) -> dict:
        try:
            response = requests.get(
                f"{self._base}{self._HOMEWORKS}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"from": from_date, "to": to_date},
                timeout=10,
            )
        except requests.RequestException:
            log.exception("get_homeworks: request failed")
            return {"error": "Homeworks request failed"}
        if not response.ok:
            return {"error": "Failed to fetch homeworks", "status_code": response.status_code}
        try:
            return response.json()
        except ValueError:
            log.exception("get_homeworks: non-JSON response (HTTP %s)", response.status_code)
            return {"error": "Invalid JSON response", "status_code": response.status_code}

    def get_komens(self, access_token: str) -> dict:
        try:
            response = requests.post(
                f"{self._base}{self._KOMENS}",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
        except requests.RequestException:
            log.exception("get_komens: request failed")
            return {"error": "Komens request failed"}
        if not response.ok:
            return {"error": "Failed to fetch komens", "status_code": response.status_code}
        try:
            return response.json()
        except ValueError:
            log.exception("get_komens: non-JSON response (HTTP %s)", response.status_code)
            return {"error": "Invalid JSON response", "status_code": response.status_code}

    def get_subject_themes(self, access_token: str, subject_id: str) -> dict:
        """Fetch theme metadata for subject_id (abbreviation, e.g. 'MAT').

        Returns {"Themes": [...]} on success, {"themes": []} on 405 (unsupported
        by the school), or {"error": ..., "status_code": ...} on other failures.
        """
        encoded = quote(subject_id, safe="")
        url = f"{self._base}{self._THEMES.format(subject_id=encoded)}"
        try:
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
        except requests.RequestException:
            log.exception("get_subject_themes: request failed for subject=%s", subject_id)
            return {"error": "Themes request failed"}
        if response.status_code == 405:
            log.debug("get_subject_themes: 405 for subject=%s (not supported)", subject_id)
            return {"themes": []}
        if response.status_code == 401:
            return {"error": "Unauthorized", "status_code": 401}
        if not response.ok:
            return {"error": "Failed to fetch themes", "status_code": response.status_code}
        try:
            return response.json()
        except ValueError:
            log.exception("get_subject_themes: non-JSON response (HTTP %s)", response.status_code)
            return {"error": "Invalid JSON response", "status_code": response.status_code}

    @staticmethod
    def classify_homework_topic(subject: str, content: str) -> str:
        """Ask OpenRouter for a 1-word topic type. Falls back to subject on any error."""
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            return subject or "Úkol"
        prompt = (
            f"Předmět: {subject}\nZadání: {content}\n\n"
            "Odpověz JEDNÍM českým slovem, které nejlépe popisuje typ tohoto úkolu "
            "(např. Čtení, Výpočet, Opakování, Esej, Projekt, Test, Překlad, Cvičení). "
            "Jen jedno slovo, bez vysvětlení."
        )
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model":       "openrouter/free",
                    "messages":    [{"role": "user", "content": prompt}],
                    "max_tokens":  10,
                },
                timeout=8,
            )
            r.raise_for_status()
            word = r.json()["choices"][0]["message"]["content"].strip().split()[0]
            return word or subject or "Úkol"
        except Exception:
            log.debug("classify_homework_topic: OpenRouter call failed, returning subject")
            return subject or "Úkol"

    def print_marks(self, access_token: str):
        data = self.get_marks(access_token)
        if "error" in data:
            print(f"CHYBA: {data['error']}")
            return

        subjects = data.get("Subjects", [])
        print(f"Celkem předmětů: {len(subjects)}\n")
        for subject in subjects:
            name    = subject["Subject"]["Name"]
            abbrev  = subject["Subject"]["Abbrev"].strip()
            average = subject["AverageText"].strip()
            print(f"📚 {name} ({abbrev}) — průměr: {average}")
            for mark in subject["Marks"]:
                print(f"   • {mark['MarkText']} | {mark['Caption']} | {mark['TypeNote']} "
                      f"| váha: {mark.get('Weight') or 'body'} | {mark['MarkDate'][:10]}")
            print()