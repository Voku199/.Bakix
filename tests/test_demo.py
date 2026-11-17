"""Ukázka: stejné stránky jako po přihlášení, ale bez session, databáze a volání školy."""

import pytest

PAGES = [
    "/ukazka/prehled",
    "/ukazka/znamky",
    "/ukazka/znamky/M?znamka=1&vaha=2",
    "/ukazka/rozvrh",
    "/ukazka/ukoly",
    "/ukazka/zpravy",
    "/ukazka/zpravy/demo-1",
    "/ukazka/absence",
    "/ukazka/uceni",
    "/ukazka/uceni?predmet=M&tema=Pythagorova+v%C4%9Bta&znamka=5",
]


@pytest.mark.parametrize("path", PAGES)
def test_demo_pages_render_without_session(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert "Set-Cookie" not in response.headers
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert response.headers["Cache-Control"] == "no-store"
    assert "Tohle je ukázka" in response.get_data(as_text=True)


def test_demo_redirects_to_overview(client):
    assert client.get("/ukazka").headers["Location"] == "/ukazka/prehled"


def test_demo_links_stay_in_demo(client):
    html = client.get("/ukazka/prehled").get_data(as_text=True)
    assert 'href="/ukazka/znamky"' in html and 'href="/znamky"' not in html


def test_demo_offers_prompt_for_bad_mark(client):
    html = client.get("/ukazka/prehled").get_data(as_text=True)
    assert "Pythagorova věta" in html and "Sepsat zadání" in html


def test_demo_has_no_ai_endpoint(client):
    assert client.post("/ukazka/uceni/vysvetlit", data={"predmet": "M"}).status_code == 404


def test_demo_unknown_subject_is_404(client):
    assert client.get("/ukazka/znamky/NEEXISTUJE").status_code == 404
