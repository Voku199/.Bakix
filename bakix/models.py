"""Databáze. Schválně malá: Bakix si neukládá známky, rozvrh, zprávy ani heslo.

- `Account`: kdo je přihlášený a zašifrovaný přístup do školy (tokeny, ne heslo).
- `AiUsage`: denní počet volání volitelné AI (kvóta).

Nový model = nová Alembic migrace (`flask --app wsgi db migrate`). Tabulky se nikdy
nezakládají při startu ani při requestu.
"""

from datetime import date, datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from bakix.extensions import db
from bakix.timeutil import utcnow


class Account(db.Model):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # HMAC(škola + uživatelské jméno). Stabilní identita bez uložení jména v čitelné podobě.
    uid: Mapped[str] = mapped_column(String(64), unique=True)
    # Hodnota v session cookie. Výměna = odhlášení ze všech zařízení.
    session_token: Mapped[str] = mapped_column(String(64), unique=True)
    # Fernet(JSON): adresa školy, tokeny Bakalářů a profil (jméno, třída, typ účtu).
    secrets: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Jen den, ne čas: stačí na mazání neaktivních účtů a neprozrazuje, kdy kdo co dělal.
    last_seen_on: Mapped[date] = mapped_column(default=lambda: utcnow().date())


class AiUsage(db.Model):
    __tablename__ = "ai_usage"
    __table_args__ = (UniqueConstraint("account_id", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    day: Mapped[date]
    requests: Mapped[int] = mapped_column(default=0)
    tokens: Mapped[int] = mapped_column(default=0)
