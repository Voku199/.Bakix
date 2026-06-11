"""One-shot script: register an OAuth client in the Bakix database.

Usage — register Knowix's existing credentials:
    python register_oauth_client.py \
        --name "Bakix Knowix" \
        --client-id ***REMOVED-LEAKED-CLIENT-ID*** \
        --client-secret ***REMOVED-LEAKED-SECRET*** \
        --redirect-uri https://knowix.bakix.cz/auth/bakix/callback

Usage — generate fresh credentials (then update Knowix .env):
    python register_oauth_client.py \
        --name "Bakix Knowix" \
        --redirect-uri https://knowix.bakix.cz/auth/bakix/callback
"""

import argparse
import hashlib
import json
import os
import secrets
import sys

# Make sure app/ is importable from this script's directory.
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import get_connection


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def register(name: str, redirect_uris: list[str],
             client_id: str | None = None,
             client_secret: str | None = None) -> tuple[str, str]:
    if not client_id:
        client_id = secrets.token_urlsafe(24)
    if not client_secret:
        client_secret = secrets.token_urlsafe(32)

    with get_connection() as db:
        existing = db.execute(
            "SELECT client_id FROM oauth_clients WHERE client_id = ?", (client_id,)
        ).fetchone()
        if existing:
            print(f"[!] Client '{client_id}' is already registered. No changes made.")
            sys.exit(0)

        db.execute(
            "INSERT INTO oauth_clients (client_id, client_secret_hash, name, redirect_uris) "
            "VALUES (?, ?, ?, ?)",
            (client_id, _sha256(client_secret), name, json.dumps(redirect_uris)),
        )

    return client_id, client_secret


def main() -> None:
    parser = argparse.ArgumentParser(description="Register an OAuth client in Bakix.")
    parser.add_argument("--name", required=True, help="Human-readable app name")
    parser.add_argument("--redirect-uri", required=True, action="append",
                        dest="redirect_uris", help="Allowed redirect URI (repeatable)")
    parser.add_argument("--client-id", default=None,
                        help="Use this client_id instead of generating one")
    parser.add_argument("--client-secret", default=None,
                        help="Use this client_secret instead of generating one")
    args = parser.parse_args()

    client_id, client_secret = register(
        name=args.name,
        redirect_uris=args.redirect_uris,
        client_id=args.client_id,
        client_secret=args.client_secret,
    )

    print(f"[OK] Client registered successfully.")
    print(f"     BAKIX_CLIENT_ID     = {client_id}")
    print(f"     BAKIX_CLIENT_SECRET = {client_secret}")
    print(f"     BAKIX_REDIRECT_URI  = {args.redirect_uris[0]}")
    if not args.client_id:
        print()
        print("Put these values in your Bakix-Knowix .env on the server.")


if __name__ == "__main__":
    main()
