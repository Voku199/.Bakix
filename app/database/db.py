import json
import logging

from app.database.connection import get_connection

log = logging.getLogger(__name__)


def upsert_all(
    user_id: str,
    school_url: str,
    enc_creds: str,
    access_token: "str | None" = None,
    refresh_token: "str | None" = None,
) -> None:
    with get_connection() as db:
        db.execute("""
            INSERT INTO saved_credentials
                (user_id, school_url, enc_creds, access_token, refresh_token)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                school_url    = excluded.school_url,
                enc_creds     = excluded.enc_creds,
                access_token  = excluded.access_token,
                refresh_token = excluded.refresh_token
        """, (user_id, school_url, enc_creds, access_token, refresh_token))
    log.info("DB write: user=%.8s school=%s", user_id, school_url)


def update_tokens(
    user_id: str,
    access_token: str,
    refresh_token: "str | None",
) -> None:
    with get_connection() as db:
        db.execute(
            "UPDATE saved_credentials SET access_token = ?, refresh_token = ? WHERE user_id = ?",
            (access_token, refresh_token, user_id),
        )
    log.debug("DB tokens updated: user=%.8s", user_id)


def fetch_row(user_id: str) -> "dict | None":
    with get_connection() as db:
        row = db.execute(
            "SELECT * FROM saved_credentials WHERE user_id = ?", (user_id,)
        ).fetchone()
    if row:
        log.debug("DB read hit: user=%.8s", user_id)
        return dict(row)
    log.debug("DB read miss: user=%.8s", user_id)
    return None


def get_settings(user_id: str) -> dict:
    from app.services.crypto import decrypt_json
    with get_connection() as db:
        row = db.execute(
            "SELECT school_url, enc_creds, settings_json, display_name FROM saved_credentials WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return {}
    try:
        username = decrypt_json(row["enc_creds"]).get("username", "")
    except Exception:
        username = ""
    prefs = json.loads(row["settings_json"]) if row["settings_json"] else {}
    return {
        "school_url":   row["school_url"],
        "username":     username,
        "display_name": row["display_name"] or "",
        **prefs,
    }


def save_settings(user_id: str, prefs: dict) -> None:
    with get_connection() as db:
        db.execute(
            "UPDATE saved_credentials SET settings_json = ? WHERE user_id = ?",
            (json.dumps(prefs), user_id),
        )
    log.debug("settings saved: user=%.8s", user_id)


def update_display_name(user_id: str, display_name: str) -> None:
    with get_connection() as db:
        db.execute(
            "UPDATE saved_credentials SET display_name = ? WHERE user_id = ?",
            (display_name, user_id),
        )
    log.debug("display_name updated: user=%.8s", user_id)


def cache_get(user_id: str, key: str, ttl: int = 300) -> "dict | list | None":
    with get_connection() as db:
        row = db.execute(
            "SELECT response_json FROM api_cache "
            "WHERE user_id=? AND cache_key=? AND cached_at > datetime('now', ?)",
            (user_id, key, f"-{ttl} seconds"),
        ).fetchone()
    if row:
        try:
            return json.loads(row["response_json"])
        except Exception:
            return None
    return None


def cache_set(user_id: str, key: str, data) -> None:
    with get_connection() as db:
        db.execute(
            "INSERT INTO api_cache (user_id, cache_key, response_json, cached_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(user_id, cache_key) DO UPDATE SET "
            "    response_json = excluded.response_json, "
            "    cached_at     = excluded.cached_at",
            (user_id, key, json.dumps(data, ensure_ascii=False)),
        )
