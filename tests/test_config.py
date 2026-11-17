"""Konfigurace: produkce se bez klíčů nespustí a CLI pro úklid funguje."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from bakix.config import ProductionConfig
from bakix.extensions import db
from bakix.models import Account
from bakix.services import accounts, crypto
from bakix.timeutil import utcnow


def test_production_requires_secrets(monkeypatch):
    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", None)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        ProductionConfig.validate()


def test_production_refuses_same_key_twice(monkeypatch):
    for name in ("SECRET_KEY", "CREDENTIALS_KEY", "SQLALCHEMY_DATABASE_URI", "PRIVACY_CONTROLLER",
                 "PRIVACY_CONTACT_EMAIL"):  # fmt: skip
        monkeypatch.setattr(ProductionConfig, name, "stejna-hodnota")
    with pytest.raises(RuntimeError, match="dva různé"):
        ProductionConfig.validate()


def test_production_cookie_is_host_prefixed():
    assert ProductionConfig.SESSION_COOKIE_NAME.startswith("__Host-")
    assert ProductionConfig.SESSION_COOKIE_SECURE is True


def test_cleanup_removes_inactive_accounts(app, logged_in):
    account = db.session.scalar(select(Account))
    account.last_seen_on = utcnow().date() - timedelta(days=200)
    db.session.commit()
    assert accounts.purge_inactive(90) == 1
    assert db.session.scalar(select(Account)) is None


def test_key_rotation(app, logged_in):
    from cryptography.fernet import Fernet

    old = app.config["CREDENTIALS_KEY"]
    new = Fernet.generate_key().decode()
    app.config["CREDENTIALS_KEY"] = f"{new},{old}"
    result = app.test_cli_runner().invoke(args=["rotate-keys"])
    assert "Přešifrováno: 1" in result.output
    app.config["CREDENTIALS_KEY"] = new  # starý klíč pryč, data pořád čitelná
    account = db.session.scalar(select(Account))
    assert crypto.decrypt_json(account.secrets)["access"] == "access-1"
