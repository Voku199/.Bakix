"""Krátká paměť na odpovědi školy. Jen v paměti procesu, nikdy na disku.

Známky a zprávy se tak nikam neukládají: po pár minutách (SCHOOL_CACHE_SECONDS), po
odhlášení nebo po restartu jsou pryč. Aplikace běží v jednom procesu s vlákny (gunicorn
1 worker × N vláken), takže jednu paměť sdílí všechny requesty.
"""

import threading
import time

MAX_ENTRIES = 5000

_lock = threading.Lock()
_store: dict[tuple[int, str], tuple[float, object]] = {}


def get(account_id: int, key: str) -> object | None:
    with _lock:
        entry = _store.get((account_id, key))
        if entry is None:
            return None
        if entry[0] <= time.monotonic():
            del _store[(account_id, key)]
            return None
        return entry[1]


def put(account_id: int, key: str, value: object, ttl: float) -> None:
    now = time.monotonic()
    with _lock:
        if len(_store) >= MAX_ENTRIES:
            for stale in [k for k, (expires, _) in _store.items() if expires <= now]:
                del _store[stale]
            if len(_store) >= MAX_ENTRIES:
                _store.clear()  # pojistka; přijde jen o zrychlení, ne o data
        _store[(account_id, key)] = (now + ttl, value)


def clear(account_id: int | None = None) -> None:
    """Zapomene data jednoho účtu (odhlášení, tlačítko Obnovit), nebo všechno (testy)."""
    with _lock:
        if account_id is None:
            _store.clear()
            return
        for key in [k for k in _store if k[0] == account_id]:
            del _store[key]
