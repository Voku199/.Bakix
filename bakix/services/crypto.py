"""Šifrování citlivých hodnot v databázi (Fernet = AES-128-CBC + HMAC-SHA256).

Klíč je CREDENTIALS_KEY, ne SECRET_KEY. Podporuje rotaci: první klíč šifruje, všechny
dešifrují; `rotate()` přešifruje starou hodnotu nejnovějším klíčem.

Nový klíč: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

import hashlib
import hmac
import json

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from flask import current_app


class DecryptError(Exception):
    """Hodnotu nejde rozšifrovat (jiný klíč, poškozená data)."""


def _keys() -> list[bytes]:
    raw = current_app.config["CREDENTIALS_KEY"] or ""
    return [part.strip().encode() for part in raw.split(",") if part.strip()]


def _fernet() -> MultiFernet:
    return MultiFernet([Fernet(key) for key in _keys()])


def encrypt_json(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data, separators=(",", ":")).encode()).decode()


def decrypt_json(token: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except (InvalidToken, ValueError) as error:
        raise DecryptError from error


def rotate(token: str) -> str:
    try:
        return _fernet().rotate(token.encode()).decode()
    except InvalidToken as error:
        raise DecryptError from error


def keyed_hash(*parts: str) -> str:
    """HMAC-SHA256 nejstarším klíčem. Na rozdíl od holého SHA-256 nejde bez klíče
    ověřit hrubou silou ("je v databázi žák X ze školy Y?"). Nejstarší klíč proto,
    aby se hodnota při rotaci (nový klíč se přidává dopředu) nezměnila."""
    key = hashlib.sha256(b"bakix-uid:" + _keys()[-1]).digest()
    return hmac.new(key, "\n".join(parts).encode(), hashlib.sha256).hexdigest()
