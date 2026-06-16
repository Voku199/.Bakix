import logging
import os
import threading

from dotenv import load_dotenv
load_dotenv()

from app import create_app

_debug = os.getenv("DEBUG", "False").strip().lower() in ("1", "true", "yes")

logging.basicConfig(
    level=logging.DEBUG if _debug else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

_instance_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance")
os.makedirs(_instance_dir, exist_ok=True)
_log_file = os.path.join(_instance_dir, "bakix.log")

from logging.handlers import RotatingFileHandler as _RFH
_fh = _RFH(_log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
_fh.setLevel(logging.DEBUG if _debug else logging.INFO)
_fh.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
logging.getLogger().addHandler(_fh)

_log = logging.getLogger("bakix.startup")

app = create_app()
app.config["DEBUG"]     = _debug
app.config["TEST_MODE"] = os.getenv("TEST", "").strip().lower() in ("1", "true", "yes")


def _send_unavailable_alert(error: str) -> None:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        _log.error("RESEND_API_KEY není nastaven — alert email nelze odeslat")
        return
    try:
        import resend
        resend.api_key = api_key
        resend.Emails.send({
            "from": os.getenv("RESEND_FROM_EMAIL", "alert@bakix.cz"),
            "to": ["vojta.kurinec@gmail.com"],
            "subject": "Bakix není spuštěný pro veřejnou dostupnost",
            "html": (
                "<h2>⚠️ Bakix není veřejně dostupný</h2>"
                "<p>Web <strong>bakix.cz</strong> není dostupný z internetu, "
                "přestože server byl spuštěn v produkčním režimu.</p>"
                f"<p><strong>Chyba:</strong> <code>{error}</code></p>"
            ),
        })
        _log.info("Alert email odeslán na vojta.kurinec@gmail.com")
    except Exception as exc:
        _log.error("Nepodařilo se odeslat alert email: %s", exc)


def _check_public_availability() -> None:
    import time
    import requests
    time.sleep(8)  # počkáme na plné spuštění serveru
    url = "https://bakix.cz"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code < 500:
            _log.info("bakix.cz je veřejně dostupný (HTTP %d)", resp.status_code)
            return
        error = f"HTTP {resp.status_code}"
    except Exception as exc:
        error = str(exc)

    _log.error("bakix.cz není veřejně dostupný: %s", error)
    _send_unavailable_alert(error)

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
        threading.Thread(target=_check_public_availability, daemon=True).start()
        log.info("Production server (waitress): http://0.0.0.0:9994")
        serve(app, host="0.0.0.0", port=9995, threads=4)
