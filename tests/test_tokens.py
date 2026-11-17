"""Obnova tokenů a chování, když škola přístup odmítne."""

from sqlalchemy import select

from bakix.extensions import db
from bakix.models import Account
from bakix.services import cache, crypto


def _secrets():
    return crypto.decrypt_json(db.session.scalar(select(Account)).secrets)


def test_expired_access_token_is_refreshed(app, client, school):
    school.expires_in = 1  # hned po přihlášení je token „skoro propadlý“
    from tests.conftest import do_login

    do_login(client)
    school.expires_in = 3600
    assert client.get("/znamky").status_code == 200
    assert ("POST", "/api/login") in school.calls[1:]
    assert _secrets()["access"] == "access-1+" and _secrets()["refresh"] == "refresh-1+"


def test_rejected_token_is_refreshed_and_request_retried(logged_in, school):
    school.reject_access.add("access-1")
    response = logged_in.get("/znamky")
    assert response.status_code == 200 and "Matematika" in response.get_data(as_text=True)
    assert _secrets()["access"] == "access-1+"


def test_refresh_refused_means_login_again(logged_in, school):
    school.reject_access.add("access-1")
    school.refresh_ok = False
    response = logged_in.get("/znamky")
    assert response.status_code == 302 and response.headers["Location"] == "/prihlaseni"
    assert logged_in.get("/prehled").headers["Location"] == "/prihlaseni"  # session je pryč


def test_one_failing_part_does_not_break_overview(logged_in, school):
    del school.data["/api/3/homeworks"]
    html = logged_in.get("/prehled").get_data(as_text=True)
    assert "Tuhle část škola v Bakalářích nemá zapnutou." in html
    assert "Matematika" in html  # známky se načetly


def test_school_data_is_cached_only_in_memory(app, logged_in, school):
    logged_in.get("/znamky")
    calls = len(school.calls)
    logged_in.get("/znamky")
    assert len(school.calls) == calls  # druhé načtení šlo z paměti
    logged_in.post("/obnovit", data={"zpet": "/znamky"})
    logged_in.get("/znamky")
    assert len(school.calls) == calls + 1
    cache.clear()


def test_refresh_redirect_stays_local(logged_in):
    for target in ("https://zlo.example", "//zlo.example", "/\\zlo.example"):
        response = logged_in.post("/obnovit", data={"zpet": target})
        assert response.headers["Location"] == "/prehled"


def test_message_html_is_never_rendered(logged_in):
    html = logged_in.get("/zpravy/m1").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html and "alert(1)" not in html
    assert "Dobrý den," in html and "<b>pozor</b>" not in html and "pozor" in html
