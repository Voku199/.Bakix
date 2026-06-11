import base64
import json
import logging

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.secret import get_credentials_key

log = logging.getLogger(__name__)

_SALT = b"bakix-creds-v1"

_fernet: "Fernet | None" = None
_active_secret: str = ""


def _get_fernet() -> Fernet:
    # Derive from the dedicated credentials key (falls back to SECRET_KEY when
    # CREDENTIALS_KEY is unset — see app/secret.py), so data encrypted on one
    # install can always be decrypted by it. No hidden default.
    global _fernet, _active_secret
    secret = get_credentials_key()
    if _fernet is None or secret != _active_secret:
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=_SALT, iterations=480_000)
        _fernet = Fernet(base64.urlsafe_b64encode(kdf.derive(secret.encode())))
        _active_secret = secret
    return _fernet


def encrypt_json(data: dict) -> str:
    return _get_fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str) -> dict:
    try:
        return json.loads(_get_fernet().decrypt(token.encode()))
    except InvalidToken:
        log.error("decrypt_json: InvalidToken — key mismatch or corrupted data (SECRET_KEY changed?)")
        raise ValueError("Credentials cannot be decrypted with the current SECRET_KEY")
