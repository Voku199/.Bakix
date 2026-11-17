"""Ochrana odchozích požadavků.

Adresa školy přichází od uživatele a server na ni posílá požadavky. Bez ochrany by šlo Bakix
namířit na vnitřní síť (SSRF). Vrstvy obrany:
  1. přihlásit se jde jen do školy z oficiálního seznamu (services/schools.py),
  2. jen https a žádná přesměrování (služby je nenásledují),
  3. tady: adresa se nesmí překládat na soukromou, loopback ani jinak vyhrazenou IP.
"""

import ipaddress
import socket
import threading
import time
from urllib.parse import urlsplit, urlunsplit

_CHECK_TTL = 600  # s
_checked: dict[str, tuple[float, bool]] = {}
_lock = threading.Lock()


class UnsafeUrl(Exception):
    """Adresa nevede na veřejný https server."""


def normalize_school_url(url: str) -> str:
    """Sjednotí zápis adresy školy: https, malá písmena v hostu, bez koncového lomítka.

    Vyhodí UnsafeUrl, když adresa není čisté https://host[/cesta] (žádné přihlašovací
    údaje, query ani fragment)."""
    url = (url or "").strip()
    if url.startswith("http://"):
        url = "https://" + url.removeprefix("http://")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise UnsafeUrl(url)
    if parts.username or parts.password or parts.query or parts.fragment:
        raise UnsafeUrl(url)
    try:
        port = parts.port
    except ValueError:
        raise UnsafeUrl(url) from None
    host = parts.hostname.lower()
    netloc = host if port in (None, 443) else f"{host}:{port}"
    return urlunsplit(("https", netloc, parts.path.rstrip("/"), "", ""))


def is_public_host(hostname: str) -> bool:
    """True, když se host překládá jen na veřejné IP adresy. Výsledek se chvíli pamatuje."""
    now = time.monotonic()
    with _lock:
        cached = _checked.get(hostname)
        if cached and cached[0] > now:
            return cached[1]
    result = _resolve_public(hostname)
    with _lock:
        _checked[hostname] = (now + _CHECK_TTL, result)
    return result


def _resolve_public(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        return False  # nejde přeložit → odmítnout
    if not infos:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            return False
    return True


def ensure_public_url(url: str) -> None:
    host = urlsplit(url).hostname
    if not host or not is_public_host(host):
        raise UnsafeUrl(url)


def reset_cache() -> None:
    with _lock:
        _checked.clear()
