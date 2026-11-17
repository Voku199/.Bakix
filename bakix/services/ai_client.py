"""Jediné místo, odkud aplikace volá AI (OpenAI-kompatibilní API). Stejný vzor jako v Knowixu.

- Bez AI_API_KEY je AI vypnutá a Bakix jen skládá zadání (services/study.py).
- AI můžou volat jen přihlášení: každý účet má denní kvótu, celá aplikace denní strop.
- Do logu jde účel, model, doba a tokeny, nikdy obsah zpráv.
- Do AI se neposílá nic, co uživatele identifikuje (jméno, škola, třída s písmenem, ID).
"""

import logging
import time
from dataclasses import dataclass
from datetime import timedelta

import requests
from flask import current_app
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from bakix.extensions import db
from bakix.models import Account, AiUsage
from bakix.timeutil import today

log = logging.getLogger(__name__)


class AIError(Exception):
    """AI teď neodpoví. Zprávu je v pořádku ukázat uživateli."""


class AIUnavailable(AIError):
    def __init__(self) -> None:
        super().__init__("AI teď neodpovídá. Zkus to za chvíli, nebo si zadání zkopíruj.")


class AIQuotaExceeded(AIError):
    def __init__(self, message: str = "Dnešní počet odpovědí od AI máš vyčerpaný. Zítra znovu!"):
        super().__init__(message)


@dataclass(frozen=True)
class Reply:
    text: str
    model: str
    tokens: int


def is_enabled() -> bool:
    return bool(current_app.config.get("AI_API_KEY"))


def remaining_today(account: Account) -> int:
    used = db.session.scalar(
        select(AiUsage.requests).where(AiUsage.account_id == account.id, AiUsage.day == today())
    )
    db.session.rollback()
    return max(current_app.config["AI_DAILY_REQUESTS_PER_USER"] - (used or 0), 0)


def purge_usage(older_than_days: int) -> int:
    cutoff = today() - timedelta(days=older_than_days)
    result = db.session.execute(delete(AiUsage).where(AiUsage.day < cutoff))
    db.session.commit()
    return result.rowcount


def ask(
    account: Account, system: str, prompt: str, *, purpose: str, max_tokens: int = 900
) -> Reply:
    """Jedna otázka, jedna odpověď. `purpose` je krátký štítek do logu ("study")."""
    config = current_app.config
    if not config.get("AI_API_KEY"):
        raise AIUnavailable
    day = today()
    _reserve(account.id, day)
    started = time.monotonic()
    try:
        reply = _call(system, prompt, max_tokens=max_tokens)
    except AIUnavailable:
        _release(account.id, day)
        raise
    _record_tokens(account.id, day, reply.tokens)
    log.info(
        "AI volání (účel=%s, model=%s, %.0f ms, tokeny=%s)",
        purpose,
        reply.model,
        (time.monotonic() - started) * 1000,
        reply.tokens,
    )
    return reply


# --- kvóty --------------------------------------------------------------------


def _reserve(account_id: int, day) -> None:
    """Zarezervuje jedno volání. Atomicky: dva souběžné requesty limit nepřekročí."""
    config = current_app.config
    per_user = config["AI_DAILY_REQUESTS_PER_USER"]

    total = db.session.scalar(
        select(func.coalesce(func.sum(AiUsage.requests), 0)).where(AiUsage.day == day)
    )
    if total >= config["AI_DAILY_REQUESTS_TOTAL"]:
        db.session.rollback()
        log.warning("Vyčerpaný celkový denní limit AI")
        raise AIQuotaExceeded("AI má na dnešek vyčerpaný limit. Zadání si můžeš zkopírovat.")

    if _increment(account_id, day, per_user):
        return
    try:
        db.session.execute(
            insert(AiUsage).values(account_id=account_id, day=day, requests=1, tokens=0)
        )
        db.session.commit()
        return
    except IntegrityError:
        db.session.rollback()  # řádek mezitím založil souběžný request
    if not _increment(account_id, day, per_user):
        raise AIQuotaExceeded


def _increment(account_id: int, day, limit: int) -> bool:
    """True = přičteno. False = řádek ještě neexistuje. Plný limit = AIQuotaExceeded."""
    updated = db.session.execute(
        update(AiUsage)
        .where(AiUsage.account_id == account_id, AiUsage.day == day, AiUsage.requests < limit)
        .values(requests=AiUsage.requests + 1)
        .execution_options(synchronize_session=False)
    ).rowcount
    if updated:
        db.session.commit()
        return True
    exists = db.session.scalar(
        select(AiUsage.id).where(AiUsage.account_id == account_id, AiUsage.day == day)
    )
    db.session.rollback()
    if exists:
        raise AIQuotaExceeded
    return False


def _release(account_id: int, day) -> None:
    """Vrátí rezervaci po selhání AI (uživatel za výpadek nemá přijít o pokus)."""
    db.session.execute(
        update(AiUsage)
        .where(AiUsage.account_id == account_id, AiUsage.day == day, AiUsage.requests > 0)
        .values(requests=AiUsage.requests - 1)
        .execution_options(synchronize_session=False)
    )
    db.session.commit()


def _record_tokens(account_id: int, day, tokens: int) -> None:
    if tokens <= 0:
        return
    db.session.execute(
        update(AiUsage)
        .where(AiUsage.account_id == account_id, AiUsage.day == day)
        .values(tokens=AiUsage.tokens + tokens)
        .execution_options(synchronize_session=False)
    )
    db.session.commit()


# --- HTTP ---------------------------------------------------------------------


def _call(system: str, prompt: str, *, max_tokens: int) -> Reply:
    config = current_app.config
    model = config["AI_MODEL"]
    try:
        response = requests.post(
            config["AI_BASE_URL"].rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {config['AI_API_KEY']}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.4,
            },
            timeout=config["AI_TIMEOUT"],
        )
    except requests.RequestException as error:
        log.warning("AI API nedostupné: %s", type(error).__name__)
        raise AIUnavailable from None

    if response.status_code != 200:
        # Tělo chyby může obsahovat části zadání, do logu jde jen kód.
        log.warning("AI API vrátilo HTTP %s", response.status_code)
        raise AIUnavailable

    try:
        payload = response.json()
        text = payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        log.warning("AI API vrátilo nečekanou odpověď")
        raise AIUnavailable from None
    if not isinstance(text, str) or not text.strip():
        raise AIUnavailable

    usage = payload.get("usage") or {}
    tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    return Reply(
        text=text.strip(),
        model=str(payload.get("model") or model),
        tokens=tokens if isinstance(tokens, int) else 0,
    )
