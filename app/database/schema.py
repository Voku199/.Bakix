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
        # push_subscriptions — create with current schema (fresh installs)
        db.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                endpoint    TEXT NOT NULL UNIQUE,
                keys_auth   TEXT NOT NULL DEFAULT '',
                keys_p256dh TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_push_subs_user
            ON push_subscriptions (user_id)
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                name        TEXT NOT NULL,
                description TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (user_id, name)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS pending_skills (
                user_id    TEXT PRIMARY KEY,
                step       INTEGER NOT NULL DEFAULT 0,
                data_json  TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Migration for existing DBs that have the old sub_json schema.
        # SQLite cannot ALTER a NOT NULL constraint, so we rename + recreate.
        _push_cols = {r[1] for r in db.execute("PRAGMA table_info(push_subscriptions)").fetchall()}
        if "sub_json" in _push_cols:  # old column present: recreate without it
            db.execute("ALTER TABLE push_subscriptions RENAME TO _push_subs_old")
            db.execute("""
                CREATE TABLE push_subscriptions (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     TEXT NOT NULL,
                    endpoint    TEXT NOT NULL UNIQUE,
                    keys_auth   TEXT NOT NULL DEFAULT '',
                    keys_p256dh TEXT NOT NULL DEFAULT '',
                    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            db.execute("""
                INSERT INTO push_subscriptions
                    (user_id, endpoint, keys_auth, keys_p256dh, created_at)
                SELECT
                    user_id,
                    endpoint,
                    COALESCE(json_extract(sub_json, '$.keys.auth'),   ''),
                    COALESCE(json_extract(sub_json, '$.keys.p256dh'), ''),
                    created_at
                FROM _push_subs_old
            """)
            db.execute("DROP TABLE _push_subs_old")
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_push_subs_user
                ON push_subscriptions (user_id)
            """)
            log.info("push_subscriptions: migrated sub_json schema to keys_auth/keys_p256dh")
    log.info("DB ready at %s", DB_PATH)
