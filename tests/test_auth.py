"""Přihlášení přes Bakaláře: co se ukládá, co ne, a kam server smí posílat požadavky."""

import pytest
from sqlalchemy import select, text

from bakix.extensions import db
from bakix.models import Account
from bakix.services import crypto
from tests.conftest import PASSWORD, SCHOOL_URL, USERNAME, do_login


def test_login_stores_tokens_but_never_password(app, client, school):
    response = do_login(client)
    assert response.status_code == 302 and response.headers["Location"] == "/prehled"

    account = db.session.scalar(select(Account))
    raw = " ".join(
        str(v) for row in db.session.execute(text("SELECT * FROM accounts")) for v in row
    )
    for secret in (PASSWORD, USERNAME, "access-1", "refresh-1", SCHOOL_URL, "Novák"):
        assert secret not in raw, f"{secret!r} leží v databázi v čitelné podobě"

    data = crypto.decrypt_json(account.secrets)
    assert data["access"] == "access-1" and data["refresh"] == "refresh-1"
    assert "password" not in data and PASSWORD not in str(data) and USERNAME not in str(data)


def test_login_cookie_flags(client, school):
    cookies = do_login(client).headers.getlist("Set-Cookie")
    session_cookie = next(c for c in cookies if c.startswith("bakix="))
    assert "HttpOnly" in session_cookie and "SameSite=Lax" in session_cookie


def test_wrong_password_shows_generic_message(client, school):
    response = do_login(client, password="spatne")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Špatné jméno nebo heslo." in html
    assert "invalid_grant" not in html  # odpověď školy se uživateli neukazuje
    assert db.session.scalar(select(Account)) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://utocnik.example",  # není v seznamu škol
        "http://169.254.169.254/latest/meta-data",  # cloud metadata
        "https://localhost:8080",
        "https://gym.bakix.test@utocnik.example",
        "",
    ],
)
def test_login_only_to_schools_from_directory(client, school, url):
    response = do_login(client, url=url)
    assert response.status_code == 200
    assert "Vyber nejdřív školu" in response.get_data(as_text=True)
    assert school.calls == []  # server nikam nic neposlal


def test_school_must_match_its_city(client, school):
    do_login(client, city="Praha")
    assert school.calls == []


def test_school_server_down(client, school):
    school.down = True
    html = do_login(client).get_data(as_text=True)
    assert "Školní server teď neodpovídá" in html and "nejede" not in html


def test_requests_never_follow_redirects(logged_in, school):
    logged_in.get("/prehled")
    assert school.kwargs and all(kw["allow_redirects"] is False for kw in school.kwargs)


def test_private_pages_require_login(client):
    for path in ("/prehled", "/znamky", "/rozvrh", "/ukoly", "/zpravy", "/uceni", "/ucet"):
        response = client.get(path)
        assert response.status_code == 302 and response.headers["Location"] == "/prihlaseni"


def test_private_pages_are_not_cached_or_indexed(logged_in):
    response = logged_in.get("/prehled")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"


def test_logout_is_post_only(logged_in):
    assert logged_in.get("/odhlasit").status_code == 405
    assert logged_in.post("/odhlasit").status_code == 302
    assert logged_in.get("/prehled").headers["Location"] == "/prihlaseni"


def test_logout_everywhere_invalidates_other_devices(app, logged_in, school):
    other = app.test_client()
    do_login(other)
    assert other.get("/prehled").status_code == 200
    logged_in.post("/ucet/odhlasit-vsude")
    assert other.get("/prehled").status_code == 302


def test_delete_account_removes_everything(logged_in):
    logged_in.post("/ucet/smazat")
    assert db.session.scalar(select(Account)) is None
    assert logged_in.get("/prehled").status_code == 302


def test_login_again_reuses_account(client, school):
    do_login(client)
    client.post("/odhlasit")
    do_login(client)
    assert len(db.session.scalars(select(Account)).all()) == 1


def test_school_search(client):
    html = client.get("/prihlaseni?q=brno").get_data(as_text=True)
    assert "ZŠ Testovací" in html and "Gymnázium Brno" in html and "Podvržená" not in html
    html = client.get("/prihlaseni/skoly?q=zdar").get_data(as_text=True)
    assert "school-list" not in html  # město bez škol
    html = client.get("/prihlaseni/skoly?q=brno+gym").get_data(as_text=True)
    assert "Gymnázium Brno" in html and "ZŠ Testovací" not in html


class TestCsrf:
    @pytest.fixture
    def app_overrides(self):
        return {"WTF_CSRF_ENABLED": True}

    def test_post_without_token_is_rejected(self, client, school):
        assert do_login(client).status_code == 400
        assert client.post("/odhlasit").status_code == 400
        assert school.calls == []


class TestRateLimit:
    @pytest.fixture
    def app_overrides(self):
        return {"RATELIMIT_ENABLED": True}

    def test_password_guessing_is_throttled(self, client, school):
        codes = [do_login(client, password=f"pokus-{n}").status_code for n in range(14)]
        assert codes[:12] == [200] * 12 and codes[12:] == [429, 429]
        # Ani správné heslo teď neprojde: limit platí na pokusy o jeden účet, ne na neúspěchy.
        assert do_login(client).status_code == 429
        # Jiný účet ze stejné adresy (spolužák ve třídě) se přihlásit může.
        assert do_login(client, username="jiny").status_code == 200
