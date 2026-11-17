"""Přihlášení přes Bakaláře a odhlášení.

Přihlašovací stránka funguje bez JavaScriptu: vyhledání školy je obyčejný GET formulář,
výběr školy odkaz. static/js/app.js jen přidává našeptávání během psaní.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_limiter.util import get_remote_address

from bakix.extensions import limiter
from bakix.services import accounts, crypto, schools
from bakix.services.bakalari import BakalariError

bp = Blueprint("auth", __name__)

SCHOOL_COOKIE = "bakix_skola"
SCHOOL_COOKIE_AGE = 180 * 24 * 60 * 60
MAX_FIELD = 200


def _target_key() -> str:
    """Limit na cílový účet: hádání hesla jednoho žáka z mnoha IP adres."""
    school = request.form.get("skola", "")[:MAX_FIELD]
    username = request.form.get("jmeno", "")[:MAX_FIELD].strip().lower()
    return "login:" + crypto.keyed_hash(school, username)


def _remembered_school() -> schools.School | None:
    """Naposledy použitá škola (cookie "město|adresa"), pokud je pořád v seznamu."""
    city, _, url = request.cookies.get(SCHOOL_COOKIE, "").partition("|")
    return schools.resolve(city, url) if city and url else None


@bp.route("/prihlaseni", methods=["GET", "POST"])
# Podle IP volněji (celá třída sedí za jednou školní adresou), podle cílového účtu přísně.
@limiter.limit("20 per minute; 120 per hour", methods=["POST"], key_func=get_remote_address)
@limiter.limit("12 per hour", methods=["POST"], key_func=_target_key)
def login():
    if accounts.current() is not None:
        return redirect(url_for("app.overview"))

    values = request.form if request.method == "POST" else request.args
    city = values.get("mesto", "")[:MAX_FIELD]
    url = values.get("skola", "")[:MAX_FIELD]
    query = request.args.get("q", "")[:MAX_FIELD].strip()

    school = schools.resolve(city, url) if city and url else None
    if school is None and not query and "zmenit" not in request.args:
        school = _remembered_school()

    if request.method == "POST":
        username = request.form.get("jmeno", "")[:MAX_FIELD].strip()
        password = request.form.get("heslo", "")[:MAX_FIELD]
        if school is None:
            flash("Vyber nejdřív školu ze seznamu.", "error")
        elif not username or not password:
            flash("Vyplň uživatelské jméno i heslo.", "error")
        else:
            try:
                accounts.login(school, username, password)
            except BakalariError as error:
                flash(str(error), "error")
            else:
                response = redirect(url_for("app.overview"))
                response.set_cookie(
                    SCHOOL_COOKIE,
                    f"{school.city}|{school.url}",
                    max_age=SCHOOL_COOKIE_AGE,
                    secure=request.is_secure,
                    httponly=True,
                    samesite="Lax",
                )
                return response

    results = schools.search(query) if query and school is None else []
    return render_template(
        "auth/login.html", school=school, query=query, results=results, searched=bool(query)
    )


@bp.get("/prihlaseni/skoly")
@limiter.limit("60 per minute")
def school_results():
    """Kousek HTML se školami pro našeptávání (static/js/app.js)."""
    query = request.args.get("q", "")[:MAX_FIELD].strip()
    return render_template(
        "auth/_schools.html", results=schools.search(query), searched=len(query) >= 2
    )


@bp.post("/odhlasit")
def logout():
    accounts.logout()
    flash("Jsi odhlášen/a.", "info")
    return redirect(url_for("public.index"))
