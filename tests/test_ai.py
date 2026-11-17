"""Volitelná AI: vypnutá bez klíče, kvóty, co se do ní posílá."""

import pytest
import requests

from bakix.services import ai_client


def test_ai_is_off_without_key(logged_in):
    html = logged_in.get("/uceni?predmet=M").get_data(as_text=True)
    assert "Tvoje zadání" in html and "Vysvětlit hned tady" not in html
    assert logged_in.post("/uceni/vysvetlit", data={"predmet": "M"}).status_code == 404


class TestWithKey:
    @pytest.fixture
    def app_overrides(self):
        return {"AI_API_KEY": "klic", "AI_DAILY_REQUESTS_PER_USER": 2}

    @pytest.fixture
    def ai(self, monkeypatch):
        sent = []

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "choices": [{"message": {"content": "## Pythagoras\n<b>a² + b² = c²</b>"}}],
                    "usage": {"total_tokens": 42},
                }

        def post(url, **kwargs):
            sent.append(kwargs["json"])
            return Response()

        monkeypatch.setattr(ai_client.requests, "post", post)
        return sent

    def test_answer_is_rendered_safely_and_request_is_anonymous(self, logged_in, ai):
        response = logged_in.post(
            "/uceni/vysvetlit", data={"predmet": "M", "tema": "Pythagorova věta"}
        )
        html = response.get_data(as_text=True)
        assert "<h3>Pythagoras</h3>" in html and "&lt;b&gt;" in html
        sent = str(ai[0])
        assert "Pythagorova věta" in sent and "8. třídy" in sent
        for personal in ("Novák", "ZŠ Testovací", "8.A", "novak", "skola.bakix.test"):
            assert personal not in sent

    def test_daily_quota(self, logged_in, ai):
        for _ in range(2):
            logged_in.post("/uceni/vysvetlit", data={"predmet": "M"})
        html = logged_in.post("/uceni/vysvetlit", data={"predmet": "M"}).get_data(as_text=True)
        assert "Dnešní počet odpovědí od AI máš vyčerpaný" in html
        assert len(ai) == 2

    def test_outage_does_not_cost_quota(self, logged_in, monkeypatch):
        def post(url, **kwargs):
            raise requests.ConnectionError

        monkeypatch.setattr(ai_client.requests, "post", post)
        for _ in range(3):
            html = logged_in.post("/uceni/vysvetlit", data={"predmet": "M"}).get_data(as_text=True)
            assert "AI teď neodpovídá" in html
