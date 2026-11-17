"""Účty: přihlášení přes Bakaláře, session, obnova tokenů, odhlášení a mazání.

Co se ukládá: zašifrovaný blok s adresou školy, tokeny Bakalářů a profilem (jméno, třída).
Co se neukládá: heslo (použije se jednou, při přihlášení), uživatelské jméno (jen jeho HMAC)
a žádná školní data.

SQLite běží v režimu BEGIN IMMEDIATE (viz extensions.py): otevřená transakce drží zámek pro
zápis. Proto každá funkce tady transakci ukončí dřív, než se volá školní server.
"""

import logging
import secrets as pysecrets
import threading
import time
from datetime import timedelta

from flask import current_app, g, session
from sqlalchemy import delete, select

from bakix.extensions import db
from bakix.models import Account
from bakix.services import cache, crypto
from bakix.services.bakalari import USER, BakalariClient, BakalariError, Tokens, Unauthorized
from bakix.services.school import Profile, parse_profile
from bakix.services.schools import School
from bakix.timeutil import utcnow

log = logging.getLogger(__name__)

SESSION_KEY = "sid"
# Token se obnoví, když mu zbývá míň než tohle (s).
REFRESH_MARGIN = 120

_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


class ReloginRequired(Exception):
    """Škola už uložený přístup nebere. Uživatel se musí přihlásit znovu."""


def _lock_for(account_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(account_id, threading.Lock())


def client_for(school_url: str) -> BakalariClient:
    return BakalariClient(school_url, timeout=current_app.config["BAKALARI_TIMEOUT"])


# --- přihlášení ---------------------------------------------------------------


def login(school: School, username: str, password: str) -> Account:
    """Ověří údaje u školy a založí (nebo obnoví) účet. Heslo po návratu nikde nezůstává.

    Vyhazuje InvalidCredentials / BakalariError s hláškou pro uživatele."""
    client = client_for(school.url)
    tokens = client.login(username, password)
    try:
        profile = parse_profile(client.get(USER, tokens.access))
    except BakalariError:
        profile = Profile()  # bez jména se obejdeme

    uid = crypto.keyed_hash(school.url, username.strip().lower())
    blob = crypto.encrypt_json(_secrets(school, tokens, profile))

    account = db.session.scalar(select(Account).where(Account.uid == uid))
    if account is None:
        account = Account(uid=uid, session_token=pysecrets.token_hex(32), secrets=blob)
        db.session.add(account)
    else:
        account.secrets = blob
        account.last_seen_on = utcnow().date()
    db.session.commit()
    cache.clear(account.id)

    session.clear()  # nová session po přihlášení (ochrana proti session fixation)
    session.permanent = True
    session[SESSION_KEY] = account.session_token
    log.info("Přihlášení účtu %s", account.uid[:8])
    return account


def _secrets(school: School, tokens: Tokens, profile: Profile) -> dict:
    return {
        "school_url": school.url,
        "school_name": profile.school_name or school.name,
        "access": tokens.access,
        "refresh": tokens.refresh,
        "expires_at": tokens.expires_at,
        "name": profile.name,
        "class_name": profile.class_name,
        "user_type": profile.user_type,
    }


# --- session ------------------------------------------------------------------


def current() -> Account | None:
    """Přihlášený účet podle session cookie (načte se jednou za request)."""
    if "account" in g:
        return g.account
    g.account = None
    token = session.get(SESSION_KEY)
    if not isinstance(token, str) or not token:
        return None
    account = db.session.scalar(select(Account).where(Account.session_token == token))
    if account is None:
        session.clear()
        db.session.rollback()
        return None
    today = utcnow().date()
    if account.last_seen_on != today:
        account.last_seen_on = today
    db.session.commit()  # ukončí transakci (a uloží případné last_seen_on)
    session.permanent = True  # klouzavých 30 dní
    g.account = account
    return account


def secrets_of(account: Account) -> dict:
    """Rozšifrovaný blok účtu. Když nejde rozšifrovat (jiný klíč), je potřeba nové přihlášení."""
    cached = g.get("account_secrets")
    if cached is not None and cached[0] == account.secrets:
        return cached[1]
    try:
        data = crypto.decrypt_json(account.secrets)
    except crypto.DecryptError:
        log.error(
            "Účet %s: přístup nejde rozšifrovat (změnil se CREDENTIALS_KEY?)", account.uid[:8]
        )
        _forget_session()
        raise ReloginRequired from None
    g.account_secrets = (account.secrets, data)
    return data


def profile_of(account: Account) -> Profile:
    data = secrets_of(account)
    return Profile(
        name=data.get("name", ""),
        class_name=data.get("class_name", ""),
        school_name=data.get("school_name", ""),
        user_type=data.get("user_type", "student"),
    )


# --- tokeny -------------------------------------------------------------------


def access_token(account: Account, *, force_refresh: bool = False) -> str:
    """Platný access token. Když se blíží konec platnosti (nebo ho škola odmítla), obnoví ho.

    Obnova běží pod zámkem účtu: refresh token jde použít jen jednou, takže dva souběžné
    requesty si ho nesmí navzájem zneplatnit."""
    data = secrets_of(account)
    if not force_refresh and data.get("expires_at", 0) - time.time() > REFRESH_MARGIN:
        return data["access"]

    rejected = data.get("access")
    with _lock_for(account.id):
        db.session.refresh(account)
        db.session.rollback()  # konec transakce před voláním školy
        data = secrets_of(account)
        fresh = data.get("expires_at", 0) - time.time() > REFRESH_MARGIN
        if fresh and data.get("access") != rejected:
            return data["access"]  # mezitím obnovil jiný request

        try:
            tokens = client_for(data["school_url"]).refresh(data.get("refresh", ""))
        except Unauthorized:
            log.info("Účet %s: škola odmítla obnovu tokenu", account.uid[:8])
            _forget_session()
            raise ReloginRequired from None

        data = {**data, "access": tokens.access, "refresh": tokens.refresh,
                "expires_at": tokens.expires_at}  # fmt: skip
        account.secrets = crypto.encrypt_json(data)
        db.session.commit()
        g.account_secrets = (account.secrets, data)
        return tokens.access


# --- odhlášení a mazání --------------------------------------------------------


def _forget_session() -> None:
    account = g.get("account")
    if account is not None:
        cache.clear(account.id)
    session.clear()
    g.account = None
    g.pop("account_secrets", None)


def logout() -> None:
    _forget_session()


def logout_everywhere(account: Account) -> None:
    """Vymění session token: odhlásí všechna zařízení včetně tohohle."""
    account.session_token = pysecrets.token_hex(32)
    db.session.commit()
    _forget_session()


def delete_account(account: Account) -> None:
    """Smaže všechno, co o účtu máme (řádek účtu, kvóty AI kaskádou, paměť)."""
    cache.clear(account.id)
    db.session.delete(account)
    db.session.commit()
    session.clear()
    g.account = None
    g.pop("account_secrets", None)


def purge_inactive(days: int) -> int:
    """Smaže účty, na které se nikdo nepřihlásil `days` dní. Vrací počet."""
    cutoff = utcnow().date() - timedelta(days=days)
    result = db.session.execute(delete(Account).where(Account.last_seen_on < cutoff))
    db.session.commit()
    return result.rowcount
