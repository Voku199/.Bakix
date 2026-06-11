"""Single source of truth for SECRET_KEY.

Both the Flask session signer (app/__init__.py) and the credential encryptor
(app/services/crypto.py) MUST derive their key from here. Previously crypto.py
read os.getenv("SECRET_KEY", "dev-insecure-change-in-prod") directly while the
session used a persisted random key — so without SECRET_KEY in the env, every
install encrypted credentials with the *same well-known constant*. This module
removes that divergence: no env var → generate once and persist in
instance/secret_key, and everyone reads the same value.
"""

import os
import secrets
from pathlib import Path

# app/secret.py → parent is app/, parent.parent is the project root.
_KEY_PATH = Path(__file__).resolve().parent.parent / "instance" / "secret_key"


def get_secret_key() -> str:
    """Return SECRET_KEY from the env, or a generated-and-persisted key."""
    if key := os.getenv("SECRET_KEY"):
        return key
    _KEY_PATH.parent.mkdir(exist_ok=True)
    if _KEY_PATH.exists():
        return _KEY_PATH.read_text().strip()
    new_key = secrets.token_hex(32)
    _KEY_PATH.write_text(new_key)
    # This file is the master secret (session signing + credential encryption).
    # Lock it down so other users/processes on the host can't read it.
    try:
        os.chmod(_KEY_PATH, 0o600)
    except OSError:
        pass
    return new_key


def get_credentials_key() -> str:
    """Key used to encrypt stored Bakaláře credentials (see crypto.py).

    Defaults to SECRET_KEY so existing installs keep decrypting unchanged, but a
    deployment can set CREDENTIALS_KEY to a *separate* secret — then a leaked
    session-signing key can't also decrypt everyone's stored passwords, and the
    two can be rotated independently.
    """
    return os.getenv("CREDENTIALS_KEY") or get_secret_key()

