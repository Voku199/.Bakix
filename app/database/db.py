import logging
import os
import sqlite3

log = logging.getLogger(__name__)

_DB_PATH = os.getenv("BAKIX_DB_PATH", "bakix.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the credentials table and migrate any missing columns."""
    with _connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS saved_credentials (
                user_id       TEXT PRIMARY KEY,
                school_url    TEXT NOT NULL,
                enc_creds     TEXT NOT NULL,
                access_token  TEXT,
                refresh_token TEXT
            )
        """)
        # Non-destructive migration: add token columns to existing installs
        for col_def in ("access_token TEXT", "refresh_token TEXT"):
            try:
                db.execute(f"ALTER TABLE saved_credentials ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass  # column already exists
    log.info("DB ready at %s", _DB_PATH)


def upsert_all(
    user_id: str,
    school_url: str,
    enc_creds: str,
    access_token: "str | None" = None,
    refresh_token: "str | None" = None,
) -> None:
    with _connect() as db:
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
    with _connect() as db:
        db.execute(
            "UPDATE saved_credentials SET access_token = ?, refresh_token = ? WHERE user_id = ?",
            (access_token, refresh_token, user_id),
        )
    log.debug("DB tokens updated: user=%.8s", user_id)


def fetch_row(user_id: str) -> "dict | None":
    with _connect() as db:
        row = db.execute(
            "SELECT * FROM saved_credentials WHERE user_id = ?", (user_id,)
        ).fetchone()
    if row:
        log.debug("DB read hit: user=%.8s", user_id)
        return dict(row)
    log.debug("DB read miss: user=%.8s", user_id)
    return None
