import json
import logging
import uuid

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
            "SELECT school_url, enc_creds, settings_json, display_name, "
            "       subscription_tier, subscription_expires_at "
            "FROM saved_credentials WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return {}
    try:
        username = decrypt_json(row["enc_creds"]).get("username", "")
    except Exception:
        username = ""
    prefs = json.loads(row["settings_json"]) if row["settings_json"] else {}
    # Effective tier (honours expiry) — keeps the settings UI in sync with the
    # rate limiter, which also uses get_subscription_tier.
    eff_tier = get_subscription_tier(user_id)
    return {
        "school_url":        row["school_url"],
        "username":          username,
        "display_name":      row["display_name"] or "",
        "subscription_tier": eff_tier,
        "subscription_expires_at": row["subscription_expires_at"] if eff_tier == "premium" else None,
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


def cache_clear(user_id: str) -> int:
    """Delete all cached API responses for one user. Returns number of rows deleted."""
    with get_connection() as db:
        cur = db.execute("DELETE FROM api_cache WHERE user_id = ?", (user_id,))
    n = cur.rowcount
    log.info("cache_clear: user=%.8s — %d rows deleted", user_id, n)
    return n


def update_subscription_tier(user_id: str, tier: str) -> None:
    with get_connection() as db:
        db.execute(
            "UPDATE saved_credentials SET subscription_tier = ? WHERE user_id = ?",
            (tier, user_id),
        )
    log.info("subscription updated: user=%.8s tier=%s", user_id, tier)


def get_subscription_tier(user_id: str) -> str:
    """Return the *effective* tier for user_id: 'free' or 'premium'.

    Premium is time-limited — if subscription_expires_at is in the past the user
    is treated as 'free' (and lazily downgraded in the DB). This is the single
    source of truth used by the rate limiter, so expiry needs no separate cron.
    """
    with get_connection() as db:
        row = db.execute(
            "SELECT subscription_tier, subscription_expires_at "
            "FROM saved_credentials WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return "free"
        tier    = row["subscription_tier"] or "free"
        expires = row["subscription_expires_at"]
        if tier == "premium" and expires:
            still_valid = db.execute(
                "SELECT ? > datetime('now')", (expires,)
            ).fetchone()[0]
            if not still_valid:
                db.execute(
                    "UPDATE saved_credentials SET subscription_tier = 'free' "
                    "WHERE user_id = ?", (user_id,)
                )
                log.info("subscription expired: user=%.8s (was premium until %s)",
                         user_id, expires)
                return "free"
    return tier


def get_subscription_info(user_id: str) -> dict:
    """Return {tier, expires_at} with the effective tier (expiry honoured)."""
    with get_connection() as db:
        row = db.execute(
            "SELECT subscription_expires_at FROM saved_credentials WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    expires = row["subscription_expires_at"] if row else None
    tier = get_subscription_tier(user_id)  # also lazily downgrades if expired
    return {"tier": tier, "expires_at": expires if tier == "premium" else None}


def grant_premium_days(user_id: str, days: int) -> str:
    """Grant `days` of premium, stacking on any remaining time.

    New expiry = max(now, current expiry) + days. Returns the new expiry string
    ("YYYY-MM-DD HH:MM:SS", UTC).
    """
    with get_connection() as db:
        row = db.execute(
            "SELECT subscription_expires_at FROM saved_credentials WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        current = row["subscription_expires_at"] if row else None
        # Anchor on whichever is later: now, or the (still-valid) current expiry.
        base_expr = "datetime('now')"
        params: tuple = ()
        if current:
            base_expr = "MAX(datetime('now'), ?)"
            params = (current,)
        new_expiry = db.execute(
            f"SELECT datetime({base_expr}, ?)", (*params, f"+{int(days)} days")
        ).fetchone()[0]
        db.execute(
            "UPDATE saved_credentials "
            "SET subscription_tier = 'premium', subscription_expires_at = ? "
            "WHERE user_id = ?",
            (new_expiry, user_id),
        )
    log.info("premium granted: user=%.8s +%d days -> %s", user_id, days, new_expiry)
    return new_expiry


# ── Payments ──────────────────────────────────────────────────────────────────

def record_payment_pending(
    user_id: str, session_id: str, amount_czk: int, days: int,
    provider: str = "stripe",
) -> None:
    """Insert a pending payment row when a checkout session is created."""
    with get_connection() as db:
        db.execute(
            "INSERT INTO payments "
            "    (user_id, provider, session_id, amount_czk, days_granted, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending') "
            "ON CONFLICT(session_id) DO NOTHING",
            (user_id, provider, session_id, amount_czk, days),
        )
    log.info("payment pending: user=%.8s session=%.20s", user_id, session_id)


def get_payment_by_session(session_id: str) -> "dict | None":
    with get_connection() as db:
        row = db.execute(
            "SELECT * FROM payments WHERE session_id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def mark_payment_paid(session_id: str, payment_intent: "str | None" = None) -> bool:
    """Flip a pending payment to 'paid'. Returns True only on the transition
    from not-paid → paid, so callers can fulfil exactly once (idempotency)."""
    with get_connection() as db:
        cur = db.execute(
            "UPDATE payments SET status = 'paid', paid_at = datetime('now'), "
            "    payment_intent = COALESCE(?, payment_intent) "
            "WHERE session_id = ? AND status != 'paid'",
            (payment_intent, session_id),
        )
    return cur.rowcount > 0


def log_ai_request(user_id: str, provider: str) -> None:
    """Record one AI request for rate-limit accounting."""
    with get_connection() as db:
        db.execute(
            "INSERT INTO ai_usage_log (user_id, provider) VALUES (?, ?)",
            (user_id, provider),
        )


def count_ai_requests(user_id: str, provider: str, since_iso: str) -> int:
    """Count AI requests for user_id/provider since since_iso (UTC ISO string)."""
    with get_connection() as db:
        row = db.execute(
            "SELECT COUNT(*) FROM ai_usage_log "
            "WHERE user_id = ? AND provider = ? AND created_at >= ?",
            (user_id, provider, since_iso),
        ).fetchone()
    return row[0] if row else 0


# ── Conversations (multiple chats per user) ─────────────────────────────────────

def create_conversation(user_id: str, title: str = "Nový chat") -> str:
    """Create a new chat for user_id and return its id."""
    conv_id = uuid.uuid4().hex
    with get_connection() as db:
        db.execute(
            "INSERT INTO conversations (id, user_id, title) VALUES (?, ?, ?)",
            (conv_id, user_id, title or "Nový chat"),
        )
    log.info("conversation created: user=%.8s conv=%s", user_id, conv_id)
    return conv_id


def list_conversations(user_id: str) -> list:
    """Return [{id, title, updated_at}] for user_id, most recent first."""
    with get_connection() as db:
        rows = db.execute(
            "SELECT id, title, updated_at FROM conversations "
            "WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
    return [{"id": r["id"], "title": r["title"], "updated_at": r["updated_at"]} for r in rows]


def count_conversations(user_id: str) -> int:
    with get_connection() as db:
        row = db.execute(
            "SELECT COUNT(*) FROM conversations WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row[0] if row else 0


def get_conversation(conversation_id: str, user_id: str) -> "dict | None":
    """Return {id, user_id, title} if owned by user_id, else None."""
    with get_connection() as db:
        row = db.execute(
            "SELECT id, user_id, title FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def rename_conversation(conversation_id: str, user_id: str, title: str) -> bool:
    with get_connection() as db:
        cur = db.execute(
            "UPDATE conversations SET title = ?, updated_at = datetime('now') "
            "WHERE id = ? AND user_id = ?",
            (title, conversation_id, user_id),
        )
    return cur.rowcount > 0


def delete_conversation(conversation_id: str, user_id: str) -> bool:
    """Delete a conversation and its messages. Returns True if it existed."""
    with get_connection() as db:
        cur = db.execute(
            "DELETE FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        if cur.rowcount:
            db.execute(
                "DELETE FROM conversation_history WHERE conversation_id = ?",
                (conversation_id,),
            )
    return cur.rowcount > 0


def set_conversation_title_if_default(conversation_id: str, title: str) -> None:
    """Set the title only while it's still the placeholder (first message names it)."""
    with get_connection() as db:
        db.execute(
            "UPDATE conversations SET title = ? "
            "WHERE id = ? AND title IN ('Nový chat', 'Můj chat', '')",
            (title[:80], conversation_id),
        )


def get_conversation_history_rows(conversation_id: str, user_id: str) -> "list | None":
    """Return raw [{role, content, timestamp}] oldest-first, or None if not owned."""
    with get_connection() as db:
        owner = db.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()
        if not owner:
            return None
        rows = db.execute(
            "SELECT role, content, timestamp FROM conversation_history "
            "WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"], "timestamp": r["timestamp"]} for r in rows]


# ── Generated pages ────────────────────────────────────────────────────────────

def create_generated_page(page_id: str, user_id: str, title: str, html: str) -> None:
    with get_connection() as db:
        db.execute(
            "INSERT INTO generated_pages (page_id, user_id, title, html) "
            "VALUES (?, ?, ?, ?)",
            (page_id, user_id, title or "AI obsah", html),
        )
    log.info("generated page created: user=%.8s page=%s", user_id, page_id)


def get_generated_page(page_id: str) -> "dict | None":
    """Return {page_id, user_id, title, html} for a page, or None."""
    with get_connection() as db:
        row = db.execute(
            "SELECT page_id, user_id, title, html FROM generated_pages WHERE page_id = ?",
            (page_id,),
        ).fetchone()
    return dict(row) if row else None


def update_generated_page_html(page_id: str, user_id: str, html: str) -> bool:
    """Overwrite a page's HTML. Returns True if a row owned by user_id changed."""
    with get_connection() as db:
        cur = db.execute(
            "UPDATE generated_pages SET html = ?, updated_at = datetime('now') "
            "WHERE page_id = ? AND user_id = ?",
            (html, page_id, user_id),
        )
    return cur.rowcount > 0


def delete_generated_page(page_id: str, user_id: str) -> bool:
    """Delete a page owned by user_id. Returns True if a row was removed."""
    with get_connection() as db:
        cur = db.execute(
            "DELETE FROM generated_pages WHERE page_id = ? AND user_id = ?",
            (page_id, user_id),
        )
    return cur.rowcount > 0


def count_generated_pages(user_id: str) -> int:
    with get_connection() as db:
        row = db.execute(
            "SELECT COUNT(*) FROM generated_pages WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row[0] if row else 0


def list_generated_pages(user_id: str) -> list:
    """Return [{page_id, topic}] for all pages owned by user_id (newest first)."""
    with get_connection() as db:
        rows = db.execute(
            "SELECT page_id, title FROM generated_pages "
            "WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [{"page_id": r["page_id"], "topic": r["title"] or "AI obsah"} for r in rows]


def cache_cleanup_old(days: int = 7) -> int:
    """Delete cache entries older than `days` days across all users. Returns row count."""
    with get_connection() as db:
        cur = db.execute(
            "DELETE FROM api_cache WHERE cached_at < datetime('now', ?)",
            (f"-{days} days",),
        )
    n = cur.rowcount
    log.info("cache_cleanup_old: deleted %d rows older than %d days", n, days)
    return n
