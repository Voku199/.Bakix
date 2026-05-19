import os
from datetime import timedelta
from flask import Flask


def create_app():
    app = Flask(__name__)

    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", os.urandom(32).hex()),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV") == "production",
    )

    from app.database.db import init_db
    init_db()

    from app.routes.auth_routes import auth_bp
    from app.routes.bakalari_routes import bakalari_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(bakalari_bp)

    return app
