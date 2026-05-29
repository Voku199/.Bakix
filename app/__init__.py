import logging
import os
import secrets
from datetime import timedelta
from pathlib import Path
from flask import Flask, redirect, render_template, request, session, url_for

log = logging.getLogger(__name__)

# Paths that must never be intercepted by the auth gate (avoids redirect loops).
_AUTH_EXEMPT = frozenset({
    "/welcome", "/onboarding",
    "/login", "/login/now", "/logout",
    "/cookies", "/privacy", "/tos",
})


def _load_secret_key() -> str:
    """Return SECRET_KEY from env, or generate-and-persist one in the instance folder.

    Using os.urandom() as the default means a new key on every restart, which
    invalidates all existing sessions. Instead we generate the key once and
    store it in instance/secret_key so it survives restarts.
    """
    if key := os.getenv("SECRET_KEY"):
        return key
    key_path = Path(__file__).parent.parent / "instance" / "secret_key"
    key_path.parent.mkdir(exist_ok=True)
    if key_path.exists():
        return key_path.read_text().strip()
    new_key = secrets.token_hex(32)
    key_path.write_text(new_key)
    return new_key


def create_app():
    app = Flask(__name__)

    app.config.update(
        SECRET_KEY=_load_secret_key(),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV") == "production",
    )

    from app.database.schema import init_db
    init_db()

    from app.services.scheduler import start_scheduler
    start_scheduler(app)

    from app.routes.auth_routes import auth_bp
    from app.routes.bakalari_routes import bakalari_bp
    from app.routes.login import login_bp
    from app.routes.proxy_routes import proxy_bp  # PROXY_Bakix-mirrored endpoints
    from app.routes.push import push_bp            # push.py — definitive blueprint
    app.register_blueprint(auth_bp)
    app.register_blueprint(bakalari_bp)
    app.register_blueprint(login_bp)
    app.register_blueprint(proxy_bp)
    app.register_blueprint(push_bp)

    # ── Page routes ───────────────────────────────────────────────────────────
    # Endpoints are "welcome" / "onboarding" so url_for("welcome") works in
    # Python code (bakalari_routes.py, login.py).

    @app.route("/welcome")
    def welcome():
        return render_template("welcome.html")

    @app.route("/onboarding")
    def onboarding():
        return render_template("onboarding.html")

    app.add_url_rule("/welcome",    endpoint="auth.welcome",    build_only=True)
    app.add_url_rule("/onboarding", endpoint="auth.onboarding", build_only=True)

    @app.route("/cookies")
    def cookies():
        return render_template("cookies.html")

    @app.route("/privacy")
    def privacy():
        return render_template("privacy.html")

    @app.route("/tos")
    def tos():
        return render_template("terms.html")

    @app.route("/prompt")
    def prompt():
        txt_path = os.path.join(app.static_folder, "macaly.txt")
        try:
            with open(txt_path, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            content = ""
        return render_template("prompt.html", content=content)

    @app.route("/sw.js")
    def service_worker():
        from flask import send_from_directory
        return send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")

    @app.context_processor
    def _inject_user_globals():
        user_id = session.get("user_id")
        vapid_public_key = os.getenv("VAPID_PUBLIC_KEY", "")
        if not user_id:
            return {"display_name": "", "vapid_public_key": vapid_public_key}
        from app.database.db import fetch_row as _fetch_row
        row = _fetch_row(user_id)
        return {
            "display_name": (row.get("display_name") or "") if row else "",
            "vapid_public_key": vapid_public_key,
        }

    @app.before_request
    def _check_auth():
        if (request.path in _AUTH_EXEMPT
                or request.path.startswith("/static/")
                or request.path.startswith("/api/")):
            return None
        if not session.get("user_id"):
            return redirect(url_for("welcome"))
        # Renew permanent flag on every request so the 30-day TTL keeps sliding.
        session.permanent = True

    return app
