"""Veřejné stránky: úvod, bezpečnost, soukromí, podmínky a soubory pro vyhledávače."""

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from bakix import design
from bakix.assets import asset_url
from bakix.services import accounts, insights, privacy, seo
from bakix.services.demo import DemoSource
from bakix.timeutil import monday_of, now

bp = Blueprint("public", __name__)


@bp.get("/")
def index():
    if accounts.current() is not None:
        return redirect(url_for("app.overview"))
    # Náhled v hero je skutečná komponenta s ukázkovými daty, ne obrázek.
    source = DemoSource()
    moment = now()
    days = source.week(monday_of(moment.date())) + source.week(monday_of(moment.date()) + seo.WEEK)
    subjects = source.subjects()
    focus = insights.focus_day(days, moment)
    response = Response(
        render_template(
            "public/welcome.html",
            focus=focus,
            states=insights.lesson_states(focus.day, moment) if focus else [],
            marks=insights.recent_marks(subjects, 4),
            tip=next(iter(insights.attention(subjects, moment.date(), 1)), None),
            faq=seo.FAQ,
            json_ld=seo.landing_json_ld(),
        )
    )
    # Přihlášený dostane přesměrování, host stránku: cache proto musí rozlišovat podle cookie.
    response.headers["Vary"] = "Cookie"
    response.headers["Cache-Control"] = "private, max-age=300"
    return response


@bp.get("/ukazka")
def demo():
    return redirect(url_for("demo.overview"))


@bp.get("/bezpecnost")
def security():
    return render_template("public/security.html", retention=privacy.retention())


@bp.get("/soukromi")
def privacy_policy():
    return render_template(
        "public/privacy.html",
        processors=privacy.processors(),
        retention=privacy.retention(),
        updated_on=privacy.UPDATED_ON,
    )


@bp.get("/podminky")
def terms():
    return render_template("public/terms.html", updated_on=privacy.UPDATED_ON)


@bp.get("/robots.txt")
def robots():
    return Response(seo.robots_txt(), mimetype="text/plain")


@bp.get("/sitemap.xml")
def sitemap():
    return Response(seo.sitemap_xml(), mimetype="application/xml")


@bp.get("/manifest.webmanifest")
def manifest():
    palette = design.current()
    response = jsonify(
        {
            "id": "/",
            "name": "Bakix – Bakaláři na jeden pohled",
            "short_name": "Bakix",
            "description": "Známky, rozvrh, úkoly a zprávy z Bakalářů přehledně pro žáky a rodiče.",
            "lang": "cs",
            "start_url": "/?zdroj=pwa",
            "scope": "/",
            "display": "standalone",
            "background_color": palette.light_bg,
            "theme_color": palette.light_bg,
            "icons": [
                {"src": asset_url("img/icon-192.png"), "sizes": "192x192", "type": "image/png"},
                {"src": asset_url("img/icon-512.png"), "sizes": "512x512", "type": "image/png"},
                {
                    "src": asset_url("img/icon-maskable.png"),
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
        }
    )
    response.mimetype = "application/manifest+json"
    response.cache_control.max_age = 86400
    return response


@bp.get("/healthz")
def healthz():
    return {"ok": True}


@bp.get("/styleguide")
def styleguide():
    if not current_app.config["STYLEGUIDE_PUBLIC"]:
        return render_template("error.html", code=404, message="Tahle stránka neexistuje."), 404
    chosen = request.args.get("paleta")
    if chosen in design.PALETTES:
        # Náhled palety bez restartu (jen ve vývoji). Natrvalo ji nastavuje BAKIX_PALETTE v .env.
        response = redirect(url_for("public.styleguide"))
        response.set_cookie(design.PREVIEW_COOKIE, chosen, samesite="Lax", httponly=True)
        return response
    shape = request.args.get("tvar")
    if shape in design.SHAPES:
        response = redirect(url_for("public.styleguide"))
        response.set_cookie(design.SHAPE_COOKIE, shape, samesite="Lax", httponly=True)
        return response
    return render_template(
        "public/styleguide.html", palettes=design.PALETTES.values(), shapes=design.SHAPES
    )
