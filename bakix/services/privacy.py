"""Podklady pro /soukromi. Text zásad se skládá z konfigurace, aby odpovídal tomu, co
aplikace opravdu dělá: zpracovatel se v zásadách objeví jen tehdy, když je zapnutý.

Nová externí služba, která dostane osobní údaje = nová položka v `processors()`.
Lhůty tady jsou stejná čísla, která vynucuje `flask --app wsgi cleanup`.
Po změně textu zásad posuň UPDATED_ON.
"""

from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

from flask import current_app

UPDATED_ON = date(2026, 11, 17)
AI_USAGE_RETENTION_DAYS = 30
SESSION_DAYS = 30


@dataclass(frozen=True)
class Processor:
    name: str
    purpose: str
    data: str


def processors() -> list[Processor]:
    config = current_app.config
    items = [
        Processor(
            name="Tvoje škola (systém Bakaláři)",
            purpose="Odtud se načítají známky, rozvrh, úkoly a zprávy.",
            data="Přihlašovací údaje při přihlášení, potom jen přístupový token.",
        ),
    ]
    if config.get("AI_API_KEY"):
        host = urlsplit(config["AI_BASE_URL"]).hostname or "poskytovatel AI"
        items.append(
            Processor(
                name=f"Poskytovatel AI ({host})",
                purpose="Odpověď na tlačítko „Vysvětlit hned tady“. Jen když na něj klikneš.",
                data="Předmět, téma a ročník. Žádné jméno, škola ani známky.",
            )
        )
    return items


def retention() -> list[tuple[str, str]]:
    days = current_app.config["INACTIVE_ACCOUNT_DAYS"]
    minutes = max(1, current_app.config["SCHOOL_CACHE_SECONDS"] // 60)
    token = f"Do odhlášení ze všech zařízení, smazání účtu, nebo {days} dní bez přihlášení."
    return [
        ("Heslo do Bakalářů", "Neukládá se. Použije se jednou při přihlášení."),
        ("Přístupový token školy (zašifrovaný)", token),
        ("Jméno, třída a název školy (zašifrované)", "Stejně jako token."),
        (
            "Známky, rozvrh, úkoly, zprávy, absence",
            f"Neukládají se. V paměti serveru nejvýš {minutes} min.",
        ),
        ("Počet dotazů na AI za den", f"{AI_USAGE_RETENTION_DAYS} dní."),
        ("Cookie s přihlášením", f"{SESSION_DAYS} dní od poslední návštěvy."),
    ]
