"""Registrace blueprintů. Routy jsou tenké: vstup ověří, zavolají služby, vykreslí šablonu."""

from flask import Flask


def register_blueprints(app: Flask) -> None:
    from bakix.routes import account, auth, public, school

    app.register_blueprint(public.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(account.bp)
    # Stejné stránky dvakrát: s daty přihlášeného účtu a jako ukázka bez přihlášení.
    app.register_blueprint(school.bp, name="app")
    app.register_blueprint(school.bp, name="demo", url_prefix="/ukazka")
