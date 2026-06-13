import logging
import os
from datetime import timedelta
from pathlib import Path

import click
from flask import Flask, redirect, render_template, request, session, url_for
from flask_babel import Babel, _

from app.secret import get_secret_key

log = logging.getLogger(__name__)

# Paths that must never be intercepted by the auth gate (avoids redirect loops).
_AUTH_EXEMPT = frozenset({
    "/welcome", "/onboarding",
    "/login", "/login/now", "/login-demo", "/logout",
    "/cookies", "/privacy", "/tos", "/security",
    "/robots.txt", "/sitemap.xml", "/sw.js",
})

# API endpoints reachable without a session. Everything else under /api/ is
# behind the auth gate by default, so a new endpoint can't be exposed by
# forgetting a session check.
_AUTH_EXEMPT_API = frozenset({
    "/api/schools/search",          # onboarding runs before login
    "/api/validate-school",
    "/api/payment/webhook",         # Stripe server-to-server, HMAC-signed
    "/api/push/vapid-public-key",   # public by definition
})

_AUTH_EXEMPT_PREFIXES = (
    "/static/", "/set-language/",
    # /oauth/authorize redirects to login itself (with ?next= back),
    # token+userinfo are authenticated by client secret / Bearer token.
    "/oauth/",
)


def _compile_translations(app) -> None:
    from pathlib import Path
    translations_dir = Path(app.root_path).parent / "translations"
    for po_path in translations_dir.glob("*/LC_MESSAGES/messages.po"):
        mo_path = po_path.with_suffix(".mo")
        if mo_path.exists() and mo_path.stat().st_mtime >= po_path.stat().st_mtime:
            continue
        try:
            from babel.messages.pofile import read_po
            from babel.messages.mofile import write_mo
            with open(po_path, "rb") as f:
                catalog = read_po(f)
            with open(mo_path, "wb") as f:
                write_mo(f, catalog)
            log.info("Compiled %s", mo_path)
        except Exception as exc:
            log.warning("Could not compile %s: %s", po_path, exc)


def create_app():
    app = Flask(__name__)

    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

    _debug = os.getenv("DEBUG", "False").strip().lower() in ("1", "true", "yes")

    app.config.update(
        SECRET_KEY=get_secret_key(),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Secure cookies everywhere except local debug (single DEBUG switch,
        # no separate FLASK_ENV to keep in sync).
        SESSION_COOKIE_SECURE=not _debug,
        # CSRF tokens are tied to the session, which slides for 30 days — so the
        # token must not expire sooner than the session it belongs to.
        WTF_CSRF_TIME_LIMIT=None,
    )

    from app.extensions import csrf, limiter
    csrf.init_app(app)
    limiter.init_app(app)

    babel = Babel()
    app.config["BABEL_TRANSLATION_DIRECTORIES"] = str(Path(app.root_path).parent / "translations")
    def _get_locale():
        # Czech is the default: Bakix is a Czech-student product, so a visitor
        # who hasn't explicitly picked a language gets Czech — matching the
        # header's language switcher, which also defaults to "cs". English is
        # opt-in via the switcher (stored in session["language"]); we
        # deliberately do NOT auto-switch on the browser's Accept-Language,
        # which would render the page in English while the header shows CS.
        lang = session.get("language")
        if lang in ("cs", "en"):
            return lang
        return "cs"
    babel.init_app(app, locale_selector=_get_locale)
    _compile_translations(app)

    from app.database.schema import init_db
    init_db()

    from app.services.scheduler import start_scheduler
    start_scheduler(app)

    from app.routes.auth_routes import auth_bp
    from app.routes.bakalari import bakalari_bp
    from app.routes.login import login_bp
    from app.routes.oauth_provider import oauth_bp  # "Přihlásit se přes Bakix"
    from app.routes.proxy_routes import proxy_bp  # PROXY_Bakix-mirrored endpoints
    from app.routes.push import push_bp            # push.py — definitive blueprint
    from app.routes.payment_routes import payment_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(bakalari_bp)
    app.register_blueprint(login_bp)
    app.register_blueprint(oauth_bp)
    app.register_blueprint(proxy_bp)
    app.register_blueprint(push_bp)
    app.register_blueprint(payment_bp)

    # The Stripe webhook is a server-to-server POST with no session cookie and
    # its own HMAC signature check — CSRF would only ever reject it.
    csrf.exempt(app.view_functions["payment.webhook"])
    # Same for the OAuth token exchange: authenticated by client_secret + PKCE.
    csrf.exempt(app.view_functions["oauth.token"])

    @app.cli.command("oauth-client-create")
    @click.argument("name")
    @click.argument("redirect_uris", nargs=-1, required=True)
    def oauth_client_create(name, redirect_uris):
        """Register an OAuth client app (e.g. Knowix). Prints the secret ONCE."""
        from app.database.oauth_db import create_client
        client_id, client_secret = create_client(name, list(redirect_uris))
        click.echo(f"client_id={client_id}")
        click.echo(f"client_secret={client_secret}")
        click.echo("Store the secret now — only its hash is kept in the DB.")

    # ── Page routes ──────────────────────────────────────────────────────────
    # Endpoints are "welcome" / "onboarding" so url_for("welcome") works in
    # Python code (app/routes/bakalari/, login.py).

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

    @app.route("/set-language/<lang>")
    def set_language(lang):
        if lang in ("cs", "en"):
            session["language"] = lang
            session.modified = True
        ref = request.referrer or ""
        if ref and ref.startswith(request.host_url):
            return redirect(ref)
        return redirect(url_for("welcome"))

    @app.route("/security")
    def security():
        return render_template("security.html")

    @app.route("/sw.js")
    def service_worker():
        from flask import send_from_directory
        return send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")

    @app.route("/robots.txt")
    def robots_txt():
        base = request.host_url.rstrip("/")
        body = (
            "User-agent: *\n"
            "Disallow: /api/\n"
            "Disallow: /login\n"
            "Disallow: /login/now\n"
            "Disallow: /login-demo\n"
            "Disallow: /logout\n"
            "Disallow: /payment/\n"
            "Disallow: /wrap\n"
            "Disallow: /set-language/\n"
            f"\nSitemap: {base}/sitemap.xml\n"
        )
        return body, 200, {"Content-Type": "text/plain; charset=utf-8"}

    @app.route("/sitemap.xml")
    def sitemap_xml():
        import datetime as _dt
        base  = request.host_url.rstrip("/")
        today = _dt.date.today().isoformat()
        pages = [
            {"loc": f"{base}/welcome",    "changefreq": "monthly", "priority": "1.0", "lastmod": today},
            {"loc": f"{base}/onboarding", "changefreq": "monthly", "priority": "0.8", "lastmod": today},
            {"loc": f"{base}/security",   "changefreq": "yearly",  "priority": "0.5", "lastmod": today},
            {"loc": f"{base}/cookies",    "changefreq": "yearly",  "priority": "0.2", "lastmod": today},
            {"loc": f"{base}/privacy",    "changefreq": "yearly",  "priority": "0.2", "lastmod": today},
            {"loc": f"{base}/tos",        "changefreq": "yearly",  "priority": "0.2", "lastmod": today},
        ]
        lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
        for p in pages:
            lines.append(
                f'  <url>\n'
                f'    <loc>{p["loc"]}</loc>\n'
                f'    <lastmod>{p["lastmod"]}</lastmod>\n'
                f'    <changefreq>{p["changefreq"]}</changefreq>\n'
                f'    <priority>{p["priority"]}</priority>\n'
                f'  </url>'
            )
        lines.append("</urlset>")
        return "\n".join(lines), 200, {"Content-Type": "application/xml; charset=utf-8"}

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

    # Allowlist mirrors the only external origins the templates load: jsDelivr
    # (KaTeX, Chart.js) and Google Fonts. 'unsafe-inline' is still needed for the
    # app's inline <script>/onclick blocks — tightening that out is a separate
    # refactor — but the policy already blocks injected external scripts, frames
    # and plugins, which is the defence-in-depth layer behind HTML sanitization.
    _CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net; "
        "img-src 'self' data: https:; "
        # jsDelivr included so DevTools can fetch source maps (*.map) for the
        # CDN scripts — those load over connect-src, not script-src.
        "connect-src 'self' https://cdn.jsdelivr.net; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )

    @app.after_request
    def _security_headers(resp):
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["Content-Security-Policy"] = _CSP
        # HSTS only in production — in local debug the app may run over plain
        # HTTP, and HSTS would pin the browser to https for a year.
        if not _debug:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return resp

    @app.before_request
    def _check_auth():
        path = request.path
        if (path in _AUTH_EXEMPT
                or path in _AUTH_EXEMPT_API
                or path.startswith(_AUTH_EXEMPT_PREFIXES)):
            return None
        if not session.get("user_id"):
            # API callers expect JSON, not a redirect to an HTML page.
            if path.startswith("/api/"):
                return {"error": "unauthorized"}, 401
            return redirect(url_for("welcome"))
        # Renew permanent flag on every request so the 30-day TTL keeps sliding.
        session.permanent = True

    return app
