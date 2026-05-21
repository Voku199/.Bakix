import logging

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
    app.run(host="0.0.0.0",debug=True, port=5050)
