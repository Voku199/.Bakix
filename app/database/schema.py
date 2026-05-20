import logging
import sqlite3

from app.database.connection import DB_PATH, get_connection

log = logging.getLogger(__name__)


def init_db() -> None:
    """Create tables and apply non-destructive column migrations."""
    with get_connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS saved_credentials (
                user_id       TEXT PRIMARY KEY,
                school_url    TEXT NOT NULL,
                enc_creds     TEXT NOT NULL,
                access_token  TEXT,
                refresh_token TEXT
            )
        """)
        for col_def in ("access_token TEXT", "refresh_token TEXT", "settings_json TEXT"):
            try:
                db.execute(f"ALTER TABLE saved_credentials ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass  # column already exists
    log.info("DB ready at %s", DB_PATH)
