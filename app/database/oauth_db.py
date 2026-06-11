"""DB layer for the OAuth 2.0 provider ("Přihlásit se přes Bakix").

Secrets never touch the disk in plaintext: client secrets, authorization codes
and access tokens are all stored as SHA-256 hashes — a leaked DB cannot be
replayed against the API. Plaintext values exist only in the HTTP exchange.
"""

import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timedelta

from app.database.connection import get_connection

log = logging.getLogger(__name__)

CODE_TTL_SECONDS = 60
TOKEN_TTL_SECONDS = 3600


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _utcnow() -> datetime:
    return datetime.utcnow()


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ── Clients ──────────────────────────────────────────────────────────────────

def create_client(name: str, redirect_uris: list[str]) -> tuple[str, str]:
    """Register a client app. Returns (client_id, client_secret) — the secret
    is shown exactly once; only its hash is persisted."""
    client_id = secrets.token_urlsafe(24)
    client_secret = secrets.token_urlsafe(32)
    with get_connection() as db:
        db.execute(
            "INSERT INTO oauth_clients (client_id, client_secret_hash, name, redirect_uris) "
            "VALUES (?, ?, ?, ?)",
            (client_id, _sha256(client_secret), name, json.dumps(redirect_uris)),
        )
    log.info("oauth: registered client %r (%s)", name, client_id)
    return client_id, client_secret


def get_client(client_id: str) -> dict | None:
    with get_connection() as db:
        row = db.execute(
            "SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,)
        ).fetchone()
    if not row:
        return None
    client = dict(row)
    client["redirect_uris"] = json.loads(client["redirect_uris"])
    return client


def verify_client_secret(client: dict, client_secret: str) -> bool:
    return hmac.compare_digest(client["client_secret_hash"], _sha256(client_secret))


# ── Authorization codes ──────────────────────────────────────────────────────

def store_code(client_id: str, user_id: str, redirect_uri: str,
               code_challenge: str, scope: str = "profile") -> str:
    """Mint a single-use authorization code. Returns the plaintext code."""
    code = secrets.token_urlsafe(32)
    with get_connection() as db:
        _cleanup(db)
        db.execute(
            "INSERT INTO oauth_codes "
            "(code_hash, client_id, user_id, redirect_uri, code_challenge, scope, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_sha256(code), client_id, user_id, redirect_uri, code_challenge, scope,
             _iso(_utcnow() + timedelta(seconds=CODE_TTL_SECONDS))),
        )
    return code


def consume_code(code: str, client_id: str) -> dict | None:
    """Burn an authorization code and return its row, or None if invalid.

    A replayed (already-used) code is an attack signal per RFC 6749 §4.1.2 —
    every token minted from it gets revoked before returning None.
    """
    code_hash = _sha256(code)
    with get_connection() as db:
        row = db.execute(
            "SELECT * FROM oauth_codes WHERE code_hash = ? AND client_id = ?",
            (code_hash, client_id),
        ).fetchone()
        if not row:
            return None
        if row["used_at"] is not None:
            db.execute(
                "UPDATE oauth_tokens SET revoked_at = ? WHERE code_hash = ? AND revoked_at IS NULL",
                (_iso(_utcnow()), code_hash),
            )
            log.warning("oauth: authorization code replay for client=%s — tokens revoked",
                        client_id)
            return None
        if row["expires_at"] < _iso(_utcnow()):
            return None
        db.execute(
            "UPDATE oauth_codes SET used_at = ? WHERE code_hash = ?",
            (_iso(_utcnow()), code_hash),
        )
    return dict(row)


# ── Access tokens ────────────────────────────────────────────────────────────

def issue_token(client_id: str, user_id: str, scope: str, code_hash: str) -> str:
    """Mint an opaque Bearer token. Returns the plaintext token."""
    token = secrets.token_urlsafe(32)
    with get_connection() as db:
        db.execute(
            "INSERT INTO oauth_tokens "
            "(token_hash, client_id, user_id, scope, code_hash, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_sha256(token), client_id, user_id, scope, code_hash,
             _iso(_utcnow() + timedelta(seconds=TOKEN_TTL_SECONDS))),
        )
    return token


def lookup_token(token: str) -> dict | None:
    """Return the token row if it is valid (exists, unexpired, unrevoked)."""
    with get_connection() as db:
        row = db.execute(
            "SELECT * FROM oauth_tokens WHERE token_hash = ?", (_sha256(token),)
        ).fetchone()
    if not row or row["revoked_at"] is not None or row["expires_at"] < _iso(_utcnow()):
        return None
    return dict(row)


def _cleanup(db) -> None:
    """Drop long-expired codes/tokens. Piggybacks on code minting so no
    scheduler job is needed; keeps a day of history for the replay check."""
    cutoff = _iso(_utcnow() - timedelta(days=1))
    db.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (cutoff,))
    db.execute("DELETE FROM oauth_tokens WHERE expires_at < ?", (cutoff,))
