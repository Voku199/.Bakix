"""Vstupní bod pro gunicorn (`gunicorn wsgi:app`) a pro `flask --app wsgi ...`."""

from bakix import create_app

app = create_app()
