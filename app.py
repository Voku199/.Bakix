import logging
import os

from dotenv import load_dotenv
load_dotenv()

from app import create_app

_debug = os.getenv("DEBUG", "False").strip().lower() in ("1", "true", "yes")

logging.basicConfig(
    level=logging.DEBUG if _debug else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

app = create_app()
app.config["DEBUG"]     = _debug
app.config["TEST_MODE"] = os.getenv("TEST", "").strip().lower() in ("1", "true", "yes")

if __name__ == "__main__":
    _base = os.path.dirname(os.path.abspath(__file__))
    log   = logging.getLogger("bakix")

    def _resolve(p: str) -> str:
        return p if os.path.isabs(p) else os.path.join(_base, p)

    if _debug:
        # ── Lokální vývoj ─────────────────────────────────────────────────y
        cert_file = os.getenv("FLASK_SSL_CERT", "").strip()
        key_file  = os.getenv("FLASK_SSL_KEY",  "").strip()

        ssl_context = None
        if cert_file and key_file:
            cert_file = _resolve(cert_file)
            key_file  = _resolve(key_file)
            if os.path.isfile(cert_file) and os.path.isfile(key_file):
                ssl_context = (cert_file, key_file)
                log.info("Dev server (HTTPS): https://0.0.0.0:9994")
            else:
                log.warning("Cert soubory nenalezeny, fallback na HTTP")

        if not ssl_context:
            log.info("Dev server (HTTP): http://0.0.0.0:9994")

        app.run(host="0.0.0.0", port=9995, debug=True, ssl_context=ssl_context)

    else:
        # ── Produkce ──────────────────────────────────────────────────────
        from waitress import serve
        log.info("Production server (waitress): http://0.0.0.0:9994")
        serve(app, host="0.0.0.0", port=9995, threads=4)
