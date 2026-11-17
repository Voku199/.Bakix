"""Bakix 2. Aplikace se vyrábí přes create_app() (app factory), stejně jako Knowix."""

import logging
import os
from typing import Any

from flask import Flask, request
from werkzeug.middleware.proxy_fix import ProxyFix

from bakix.config import get_config


def create_app(config_name: str | None = None, overrides: dict[str, Any] | None = None) -> Flask:
    """Vyrobí aplikaci. `overrides` přepíše hodnoty z configu (hlavně pro testy)."""
    config = get_config(config_name)
    config.validate()

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config)
    if overrides:
        app.config.update(overrides)
    os.makedirs(app.instance_path, exist_ok=True)

    _configure_logging(app)

    if app.config["PROXY_COUNT"]:
        n = app.config["PROXY_COUNT"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n)  # type: ignore[method-assign]

    from bakix import models  # noqa: F401  (registrace modelů pro Alembic)
    from bakix.assets import register_assets
    from bakix.cli import cleanup, rotate_keys
    from bakix.errors import register_error_handlers
    from bakix.extensions import configure_sqlite_file, csrf, db, limiter, migrate
    from bakix.routes import register_blueprints
    from bakix.security import register_security_headers

    db.init_app(app)
    with app.app_context():
        configure_sqlite_file(db.engine)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)

    app.cli.add_command(cleanup)
    app.cli.add_command(rotate_keys)
    register_blueprints(app)
    register_error_handlers(app)
    register_security_headers(app)
    register_assets(app)
    _register_template_helpers(app)

    return app


def _register_template_helpers(app: Flask) -> None:
    from bakix import design, timeutil
    from bakix.services import accounts, insights, seo

    app.add_template_filter(timeutil.format_date, "date")
    app.add_template_filter(timeutil.relative_day, "relative_day")
    app.add_template_global(insights.plural, "plural")
    app.add_template_global(seo.public_url, "public_url")

    @app.template_filter("clock")
    def clock(value) -> str:
        return f"{value:%-H:%M}" if value else ""

    @app.template_filter("decimal")
    def decimal(value: float, digits: int = 2) -> str:
        return f"{value:.{digits}f}".replace(".", ",")

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        account = accounts.current()
        # "app" = stránky přihlášeného, "demo" = ukázka. Podle toho vedou odkazy v menu.
        space = request.blueprint if request.blueprint in ("app", "demo") else "app"
        return {
            "palette": design.current(),
            "shape": design.current_shape(),
            "account": account,
            "space": space,
            "in_demo": request.blueprint == "demo",
            "today": timeutil.today(),
        }


def _configure_logging(app: Flask) -> None:
    level = logging.DEBUG if app.debug else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    app.logger.setLevel(level)
    # urllib3 na úrovni DEBUG vypisuje adresy požadavků; do logu nepatří ani ve vývoji.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
