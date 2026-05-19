import logging

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://zstsobra.bakalari.cz"
USERNAME = "Kurin37221"
PASSWORD = "83VcyxxJ"


class BakalariService:

    def __init__(self, base_url: str = BASE_URL):
        self._base = base_url.rstrip("/")

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

    def login(self, username: str = USERNAME, password: str = PASSWORD) -> dict:
        response = requests.post(
            f"{self._base}/api/login",
            data={
                "grant_type": "password",
                "username":   username,
                "password":   password,
                "client_id":  "ANDR",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
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
        response = requests.get(
            f"{self._base}/api/3/marks",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if not response.ok:
            return {"error": "Failed to fetch marks", "status_code": response.status_code}
        return response.json()

    def get_timetable(self, access_token: str) -> dict:
        response = requests.get(
            f"{self._base}/api/3/timetable/actual",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if not response.ok:
            return {"error": "Failed to fetch timetable", "status_code": response.status_code}
        return response.json()

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


if __name__ == "__main__":
    svc = BakalariService()
    print("Přihlašuji se...")
    result = svc.login()

    if "error" in result:
        print(f"CHYBA pri prihlaseni: {result['error']}")
        print(f"Status kod: {result['status_code']}")
        print(f"Detail: {result['detail']}")
    else:
        print("Prihlaseni uspesne!")
        print(f"Access token: {result['access_token'][:30]}...")
        print("\nNacitam znamky...")
        svc.print_marks(result["access_token"])

        print("\nNacitam suplovani...")
        subs = svc.get_substitutions_from_timetable(result["access_token"])
        if isinstance(subs, dict) and "error" in subs:
            print(f"CHYBA: {subs['error']} (status {subs['status_code']})")
        else:
            print(f"Suplovani OK — pocet zmen: {len(subs)}")
