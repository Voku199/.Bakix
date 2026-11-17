"""Jednotné zpracování chyb: uživatel dostane kód a lidskou hlášku, traceback jde jen do logu."""

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException

MESSAGES = {
    400: "Požadavek se nepodařilo zpracovat. Zkus stránku načíst znovu.",
    403: "Sem nemáš přístup.",
    404: "Tahle stránka neexistuje.",
    405: "Tahle akce tu není povolená.",
    413: "Požadavek je moc velký.",
    429: "Moc požadavků najednou. Chvíli počkej a zkus to znovu.",
    500: "Něco se pokazilo na naší straně. Zkus to za chvíli znovu.",
}


def _wants_json() -> bool:
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    return (
        best == "application/json"
        and request.accept_mimetypes[best] > (request.accept_mimetypes["text/html"])
    )


def _error_response(code: int):
    message = MESSAGES.get(code, MESSAGES[500] if code >= 500 else MESSAGES[400])
    if _wants_json():
        return jsonify(error=code, message=message), code
    return render_template("error.html", code=code, message=message), code


def register_error_handlers(app: Flask) -> None:
    from bakix.services.accounts import ReloginRequired

    @app.errorhandler(ReloginRequired)
    def handle_relogin(_exc: ReloginRequired):
        # Škola už náš token nebere (změna hesla, dlouhá neaktivita). Session je pryč,
        # účet odhlásila služba; uživatel se jen znovu přihlásí.
        flash("Škola tě odhlásila. Přihlas se prosím znovu.", "info")
        return redirect(url_for("auth.login"))

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc: HTTPException):
        return _error_response(exc.code or 500)

    if app.debug:
        return  # ve vývoji necháme výjimky probublat do debuggeru

    @app.errorhandler(Exception)
    def handle_unexpected_exception(exc: Exception):
        app.logger.exception("Neošetřená výjimka: %s %s", request.method, request.path)
        return _error_response(500)
