"""Spuštění Bakixu jedním příkazem: `python app.py`.

Všechno se nastavuje v souboru .env (vzor: .env.example):

    BAKIX_ENV   development (výchozí pro app.py) | production
    HOST        na jaké adrese poslouchat (výchozí 127.0.0.1)
    PORT        na jakém portu (výchozí 5000)
    THREADS     počet vláken v produkci (výchozí 8)

Co skript udělá:
  1. načte .env,
  2. připraví databázi (provede migrace, takže první spuštění nic dalšího nepotřebuje),
  3. spustí server: ve vývoji vestavěný server Flasku s automatickým načítáním změn,
     v produkci waitress (funguje na Windows i Linuxu, jeden proces s vlákny).

Bez .env se spustí vývojový režim na http://127.0.0.1:5000. Na veřejný server patří
BAKIX_ENV=production a vyplněné klíče; bez nich produkce odmítne nastartovat.
"""

import ipaddress
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# Příkaz `flask …` a gunicorn mají výchozí produkci. `python app.py` bez .env je skoro vždycky
# první spuštění na vlastním počítači, proto tady vývojový režim.
if "BAKIX_ENV" not in os.environ:
    os.environ["BAKIX_ENV"] = "development"
    print("BAKIX_ENV není nastavené: spouštím vývojový režim. Na server dej BAKIX_ENV=production.")

from bakix import create_app  # noqa: E402  (až po načtení .env)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main() -> int:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    threads = int(os.environ.get("THREADS", "8"))

    try:
        app = create_app()
    except RuntimeError as error:
        print(f"Bakix se nespustil: {error}", file=sys.stderr)
        return 1

    # Reloader spouští skript dvakrát; migrace stačí jednou, v rodičovském procesu.
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        from flask_migrate import upgrade

        with app.app_context():
            upgrade(directory=str(ROOT / "migrations"))

    if app.debug:
        # Interaktivní debugger umí spouštět kód, proto jen na vlastním počítači.
        local = _is_loopback(host)
        if not local:
            print("Vývojový režim na veřejné adrese: debugger a automatické načítání jsou vypnuté.")
        print(
            f"Bakix (vývoj) běží na http://{host}:{port}  ·  ukázka: /ukazka  ·  styl: /styleguide"
        )
        app.run(host=host, port=port, debug=local, use_debugger=local, use_reloader=local)
        return 0

    from waitress import serve

    print(f"Bakix (produkce) běží na http://{host}:{port}, {threads} vláken")
    # Jeden proces: paměť na školní data, zámky obnovy tokenů i limity pak platí pro celý web.
    serve(app, host=host, port=port, threads=threads, ident=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
