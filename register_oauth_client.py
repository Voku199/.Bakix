"""One-shot script: register (or rotate) an OAuth client in the Bakix database.

Usage — generate fresh credentials for a new client (then update its .env):
    python register_oauth_client.py \
        --name "Bakix Knowix" \
        --redirect-uri https://knowix.bakix.cz/auth/bakix/callback

Usage — rotate an existing client's secret (e.g. after a leak), keeping the
same client_id:
    python register_oauth_client.py \
        --name "Bakix Knowix" \
        --redirect-uri https://knowix.bakix.cz/auth/bakix/callback \
        --client-id <existing-client-id> \
        --rotate

Never pass real credentials as literal arguments in shared shell history /
docs — generate them and copy the printed values directly into .env.
"""

import argparse
import hashlib
import json
import os
import secrets
import sys

from dotenv import load_dotenv
load_dotenv()

# Make sure app/ is importable from this script's directory.
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import DB_PATH, get_connection


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def register(name: str, redirect_uris: list[str],
             client_id: str | None = None,
             client_secret: str | None = None,
             rotate: bool = False) -> tuple[str, str]:
    if not client_id:
        client_id = secrets.token_urlsafe(24)
    if not client_secret:
        client_secret = secrets.token_urlsafe(32)

    with get_connection() as db:
        table = db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'oauth_clients'"
        ).fetchone()
        if not table:
            print(f"[!] No 'oauth_clients' table in {DB_PATH}.")
            print("    This DB hasn't been initialized yet, or BAKIX_DB_PATH points "
                  "somewhere unexpected. Run the app once (so init_db() creates the "
                  "schema) or check BAKIX_DB_PATH in .env, then retry.")
            sys.exit(1)

        existing = db.execute(
            "SELECT client_id FROM oauth_clients WHERE client_id = ?", (client_id,)
        ).fetchone()

        if existing and not rotate:
            print(f"[!] Client '{client_id}' is already registered. No changes made. "
                  "Pass --rotate to replace its secret.")
            sys.exit(0)

        if existing and rotate:
            db.execute(
                "UPDATE oauth_clients SET client_secret_hash = ?, name = ?, redirect_uris = ? "
                "WHERE client_id = ?",
                (_sha256(client_secret), name, json.dumps(redirect_uris), client_id),
            )
        else:
            db.execute(
                "INSERT INTO oauth_clients (client_id, client_secret_hash, name, redirect_uris) "
                "VALUES (?, ?, ?, ?)",
                (client_id, _sha256(client_secret), name, json.dumps(redirect_uris)),
            )

    return client_id, client_secret


def main() -> None:
    parser = argparse.ArgumentParser(description="Register or rotate an OAuth client in Bakix.")
    parser.add_argument("--name", required=True, help="Human-readable app name")
    parser.add_argument("--redirect-uri", required=True, action="append",
                        dest="redirect_uris", help="Allowed redirect URI (repeatable)")
    parser.add_argument("--client-id", default=None,
                        help="Existing client_id to rotate, or a fixed id to use for a new client")
    parser.add_argument("--rotate", action="store_true",
                        help="If --client-id already exists, replace its secret instead of erroring")
    args = parser.parse_args()

    print(f"[i] Using database: {DB_PATH}")

    client_id, client_secret = register(
        name=args.name,
        redirect_uris=args.redirect_uris,
        client_id=args.client_id,
        rotate=args.rotate,
    )

    print(f"[OK] Client {'rotated' if args.rotate else 'registered'} successfully.")
    print(f"     BAKIX_CLIENT_ID     = {client_id}")
    print(f"     BAKIX_CLIENT_SECRET = {client_secret}")
    print(f"     BAKIX_REDIRECT_URI  = {args.redirect_uris[0]}")
    print()
    print("Put these values in the client app's .env on the server.")


if __name__ == "__main__":
    main()
