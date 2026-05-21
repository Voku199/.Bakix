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
        for col_def in ("access_token TEXT", "refresh_token TEXT", "settings_json TEXT", "display_name TEXT"):
            try:
                db.execute(f"ALTER TABLE saved_credentials ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass  # column already exists
        db.execute("""
            CREATE TABLE IF NOT EXISTS gemini_cache (
                user_id    TEXT NOT NULL,
                query_hash TEXT NOT NULL,
                response   TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, query_hash)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS conversation_history (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   TEXT NOT NULL,
                role      TEXT NOT NULL CHECK(role IN ('user', 'model')),
                content   TEXT NOT NULL,
                timestamp TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_conv_hist_user
            ON conversation_history (user_id, id DESC)
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS api_cache (
                user_id       TEXT NOT NULL,
                cache_key     TEXT NOT NULL,
                response_json TEXT NOT NULL,
                cached_at     TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, cache_key)
            )
        """)
    log.info("DB ready at %s", DB_PATH)
