"""Instance rozšíření. Inicializují se v create_app(), ať jde aplikaci vyrobit víckrát (testy)."""

import sqlite3

from flask import g
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import Engine, MetaData, event
from sqlalchemy.orm import DeclarativeBase

# Pevné názvy constraintů, aby je Alembic uměl později přejmenovat i smazat.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@event.listens_for(Engine, "connect")
def _sqlite_foreign_keys(dbapi_connection, _record) -> None:
    """SQLite má cizí klíče (a ON DELETE CASCADE) ve výchozím stavu vypnuté."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SQLITE_BUSY_TIMEOUT = 10  # s


def configure_sqlite_file(engine: Engine) -> None:
    """Produkční nastavení pro SQLite v souboru: WAL a zámek pro zápis hned na začátku transakce.

    In-memory DB (testy) se nenastavuje: tam sdílí víc sessions jedno spojení.
    """
    if engine.url.get_backend_name() != "sqlite" or engine.url.database in (None, "", ":memory:"):
        return

    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection, _record) -> None:
        dbapi_connection.isolation_level = None  # transakce řídí SQLAlchemy
        cursor = dbapi_connection.cursor()
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT * 1000}")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(connection) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def rate_limit_key() -> str:
    """Přihlášení se omezují podle účtu, anonymové podle IP (skutečná IP přijde z ProxyFix)."""
    account = g.get("account")
    if account is not None:
        return f"account:{account.id}"
    return get_remote_address()


# expire_on_commit=False: účet zůstane použitelný i po commitu, kterým se před voláním školy
# ukončuje transakce (viz services/accounts.py).
db = SQLAlchemy(model_class=Base, session_options={"expire_on_commit": False})
migrate = Migrate(render_as_batch=True)  # SQLite neumí většinu ALTER TABLE
csrf = CSRFProtect()
limiter = Limiter(key_func=rate_limit_key)
