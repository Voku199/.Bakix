import json
import logging
import os
import sqlite3
import uuid

from app.database.connection import DB_PATH, get_connection

log = logging.getLogger(__name__)


def _migrate_generated_pages_from_files(db) -> None:
    """One-time import of the old file-based generated pages into the DB.

    Reads instance/generated/index.json + the matching <page_id>.html files,
    inserts any rows not already present, then renames index.json so the import
    never runs twice. Silent and best-effort — a missing/corrupt index is fine.
    """
    gen_dir    = os.path.join(os.path.dirname(DB_PATH), "generated")
    index_path = os.path.join(gen_dir, "index.json")
    if not os.path.exists(index_path):
        return
    try:
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
    except (OSError, json.JSONDecodeError):
        return

    imported = 0
    for page_id, meta in index.items():
        html_path = os.path.join(gen_dir, f"{page_id}.html")
        if not os.path.isfile(html_path):
            continue
        try:
            with open(html_path, encoding="utf-8") as f:
                html = f.read()
        except OSError:
            continue
        cur = db.execute(
            "INSERT OR IGNORE INTO generated_pages (page_id, user_id, title, html) "
            "VALUES (?, ?, ?, ?)",
            (page_id, meta.get("user_id", ""), meta.get("title") or "AI obsah", html),
        )
        imported += cur.rowcount

    try:
        os.rename(index_path, index_path + ".migrated")
    except OSError:
        pass
    if imported:
        log.info("generated_pages: migrated %d page(s) from files to DB", imported)


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
        for col_def in (
            "access_token TEXT",
            "refresh_token TEXT",
            "settings_json TEXT",
            "display_name TEXT",
            "subscription_tier TEXT NOT NULL DEFAULT 'free'",
            # ISO datetime (UTC, "YYYY-MM-DD HH:MM:SS") when premium runs out.
            # NULL = no premium / never paid. Used to auto-downgrade on expiry.
            "subscription_expires_at TEXT",
        ):
            try:
                db.execute(f"ALTER TABLE saved_credentials ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass  # column already exists
        # payments — one row per checkout attempt (audit trail + idempotent fulfilment)
        db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        TEXT NOT NULL,
                provider       TEXT NOT NULL DEFAULT 'stripe',
                session_id     TEXT UNIQUE,
                payment_intent TEXT,
                amount_czk     INTEGER,
                days_granted   INTEGER,
                status         TEXT NOT NULL DEFAULT 'pending',
                created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                paid_at        TEXT
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_payments_user
            ON payments (user_id, id DESC)
        """)
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
        # ── Multiple chats per user (ChatGPT-style conversation list) ──────────
        db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id         TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                title      TEXT NOT NULL DEFAULT 'Nový chat',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversations_user
            ON conversations (user_id, updated_at DESC)
        """)
        # Scope each history row to a conversation. Older installs have rows
        # without it → backfill one "legacy" conversation per affected user.
        try:
            db.execute("ALTER TABLE conversation_history ADD COLUMN conversation_id TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_conv_hist_conversation
            ON conversation_history (conversation_id, id DESC)
        """)
        _orphan_users = [
            r[0] for r in db.execute(
                "SELECT DISTINCT user_id FROM conversation_history WHERE conversation_id IS NULL"
            ).fetchall()
        ]
        for _uid in _orphan_users:
            _conv_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO conversations (id, user_id, title) VALUES (?, ?, ?)",
                (_conv_id, _uid, "Můj chat"),
            )
            db.execute(
                "UPDATE conversation_history SET conversation_id = ? "
                "WHERE user_id = ? AND conversation_id IS NULL",
                (_conv_id, _uid),
            )
        if _orphan_users:
            log.info("conversations: backfilled legacy chat for %d user(s)", len(_orphan_users))
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
        db.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL,
                event_type TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_activity_log_user
            ON activity_log (user_id, created_at DESC)
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS ai_usage_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL,
                provider   TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_ai_usage_log_user
            ON ai_usage_log (user_id, provider, created_at DESC)
        """)
        # AI-generated study pages. Previously stored as files in
        # instance/generated/<id>.html + index.json (no locking → corruption
        # under concurrency); now one row per page.
        db.execute("""
            CREATE TABLE IF NOT EXISTS generated_pages (
                page_id    TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                title      TEXT NOT NULL DEFAULT 'AI obsah',
                html       TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_generated_pages_user
            ON generated_pages (user_id, created_at DESC)
        """)
        # ── OAuth 2.0 provider ("Přihlásit se přes Bakix") ─────────────────────
        # Secrets are stored hashed (sha256) — see app/database/oauth_db.py.
        db.execute("""
            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id          TEXT PRIMARY KEY,
                client_secret_hash TEXT NOT NULL,
                name               TEXT NOT NULL,
                redirect_uris      TEXT NOT NULL,
                created_at         TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS oauth_codes (
                code_hash      TEXT PRIMARY KEY,
                client_id      TEXT NOT NULL,
                user_id        TEXT NOT NULL,
                redirect_uri   TEXT NOT NULL,
                code_challenge TEXT NOT NULL,
                scope          TEXT NOT NULL DEFAULT 'profile',
                expires_at     TEXT NOT NULL,
                used_at        TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS oauth_tokens (
                token_hash TEXT PRIMARY KEY,
                client_id  TEXT NOT NULL,
                user_id    TEXT NOT NULL,
                scope      TEXT NOT NULL DEFAULT 'profile',
                code_hash  TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
            )
        """)
        _migrate_generated_pages_from_files(db)
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
