import logging
import os
import sqlite3

log = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.getenv("BAKIX_DB_PATH", os.path.join(_ROOT, "instance", "app.db"))


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    # timeout: block instead of failing immediately when another writer holds
    # the lock (waitress threads + APScheduler + push daemon threads all write).
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL lets readers and a writer work concurrently (instead of "database is
    # locked"); the pragmas are connection-scoped but journal_mode persists.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn
