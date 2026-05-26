import logging
import os

from dotenv import load_dotenv
load_dotenv()

from app import create_app

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

app = create_app()

if __name__ == "__main__":
    _base = os.path.dirname(os.path.abspath(__file__))

    def _resolve(p: str) -> str:
        return p if os.path.isabs(p) else os.path.join(_base, p)

    cert_file = _resolve(os.getenv("FLASK_SSL_CERT", "").strip())
    key_file = _resolve(os.getenv("FLASK_SSL_KEY", "").strip())

    ssl_context = None
    if cert_file and key_file:
        ssl_context = (cert_file, key_file)
        logging.getLogger("bakix.dev").info("Starting HTTPS dev server on https://%s:%s", "0.0.0.0", 5050)
    else:
        logging.getLogger("bakix.dev").info("Starting HTTP dev server on http://%s:%s", "0.0.0.0", 5050 )

    app.run(host="0.0.0.0", port=5050, ssl_context=ssl_context)