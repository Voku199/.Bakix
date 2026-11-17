import contextvars
import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from flask import has_app_context
from flask.testing import FlaskClient

from bakix import create_app
from bakix.extensions import db
from bakix.services import cache, net, schools

SCHOOL_URL = "https://skola.bakix.test"
SCHOOL_CITY = "Brno"
SCHOOL_NAME = "ZŠ Testovací"
USERNAME = "novak"
PASSWORD = "tajne-heslo-123"


class IsolatedClient(FlaskClient):
    """Každý request běží ve vlastním app contextu, stejně jako v produkci (vzor z Knowixu)."""

    def open(self, *args, **kwargs):
        if not has_app_context():
            return super().open(*args, **kwargs)
        db.session.commit()
        response = contextvars.Context().run(super().open, *args, **kwargs)
        db.session.expire_all()
        return response


class _Raw:
    def __init__(self, body: bytes):
        self._body = io.BytesIO(body)

    def read(self, amount=None, decode_content=False):
        return self._body.read(amount)


class FakeResponse:
    def __init__(self, status: int, payload=None):
        self.status_code = status
        self.raw = _Raw(json.dumps(payload if payload is not None else {}).encode())

    def close(self):
        pass


class FakeSchool:
    """Falešný školní server Bakalářů. Nahrazuje requests.Session.request."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.kwargs: list[dict] = []
        self.access = "access-1"
        self.refresh = "refresh-1"
        self.refresh_ok = True
        self.expires_in = 3600
        self.reject_access: set[str] = set()
        self.down = False
        self.data = {
            "/api/3/user": {
                "FullName": "Novák Jan, 8.A",
                "Class": {"Abbrev": "8.A"},
                "SchoolOrganizationName": SCHOOL_NAME,
                "UserType": "student",
            },
            "/api/3/marks": {
                "Subjects": [
                    {
                        "Subject": {"Id": "M", "Abbrev": "M", "Name": "Matematika"},
                        "AverageText": "3,50",
                        "Marks": [
                            {"Id": "a", "MarkText": "5", "Weight": 3, "Caption": "Písemka",
                             "Theme": "Pythagorova věta", "MarkDate": "2026-11-16T00:00:00+01:00"},
                            {"Id": "b", "MarkText": "2", "Weight": 1, "Caption": "Aktivita",
                             "MarkDate": "2026-10-01T00:00:00+02:00"},
                        ],
                    }
                ]
            },
            "/api/3/timetable/actual": {
                "Hours": [{"Id": 2, "Caption": "1", "BeginTime": "8:00", "EndTime": "8:45"}],
                "Subjects": [{"Id": "M", "Abbrev": "M", "Name": "Matematika"}],
                "Teachers": [{"Id": "T1", "Abbrev": "Pr", "Name": "Mgr. Procházková"}],
                "Rooms": [{"Id": "R1", "Abbrev": "203"}],
                "Days": [],
            },
            "/api/3/homeworks": {"Homeworks": []},
            "/api/3/komens/messages/received": {
                "Messages": [
                    {"Id": "m1", "Title": "Schůzky", "Sender": {"Name": "Mgr. Nová"},
                     "SentDate": "2026-11-16T10:00:00+01:00", "Read": False,
                     "Text": "<p>Dobrý den,</p><script>alert(1)</script><b>pozor</b>"}
                ]
            },
            "/api/3/absence/student": {"Absences": [], "AbsencesPerSubject": []},
        }  # fmt: skip

    def tokens(self):
        return {"access_token": self.access, "refresh_token": self.refresh,
                "expires_in": self.expires_in}  # fmt: skip

    def __call__(self, _session, method, url, **kwargs):
        parts = urlsplit(url)
        assert f"{parts.scheme}://{parts.netloc}" == SCHOOL_URL, f"nečekaný cíl {url}"
        self.calls.append((method, parts.path))
        self.kwargs.append(kwargs)
        if self.down:
            raise requests.ConnectionError("nejede")
        if parts.path == "/api/login":
            form = kwargs["data"]
            if form["grant_type"] == "password":
                if form["username"] != USERNAME or form["password"] != PASSWORD:
                    return FakeResponse(400, {"error": "invalid_grant"})
                return FakeResponse(200, self.tokens())
            if not self.refresh_ok or form["refresh_token"] != self.refresh:
                return FakeResponse(400, {"error": "invalid_grant"})
            self.access, self.refresh = self.access + "+", self.refresh + "+"
            return FakeResponse(200, self.tokens())
        token = kwargs["headers"]["Authorization"].removeprefix("Bearer ")
        if token != self.access or token in self.reject_access:
            return FakeResponse(401)
        if parts.path in self.data:
            return FakeResponse(200, self.data[parts.path])
        return FakeResponse(404)


def _fake_directory(url: str):
    path = urlsplit(url).path
    if path.endswith("/municipality"):
        return [{"name": SCHOOL_CITY}, {"name": "Praha"}, {"name": "Žďár nad Sázavou"}]
    city = parse_qs("x=" + path.rsplit("/", 1)[1])["x"][0]
    if city == SCHOOL_CITY:
        return {"schools": [
            {"name": SCHOOL_NAME, "schoolUrl": SCHOOL_URL + "/"},
            {"name": "Gymnázium Brno", "schoolUrl": "https://gym.bakix.test"},
            {"name": "Podvržená", "schoolUrl": "ftp://zlo.test"},
        ]}  # fmt: skip
    return {"schools": []}


@pytest.fixture
def app_overrides():
    return {}


@pytest.fixture
def app(app_overrides, monkeypatch):
    monkeypatch.setattr(schools, "_fetch", _fake_directory)
    monkeypatch.setattr(net, "_resolve_public", lambda host: True)
    app = create_app("testing", app_overrides)
    app.test_client_class = IsolatedClient
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
    cache.clear()
    schools.reset_cache()
    net.reset_cache()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def school(monkeypatch):
    fake = FakeSchool()
    monkeypatch.setattr(requests.Session, "request", lambda *a, **kw: fake(*a, **kw))
    return fake


def do_login(client, username=USERNAME, password=PASSWORD, url=SCHOOL_URL, city=SCHOOL_CITY):
    return client.post(
        "/prihlaseni", data={"mesto": city, "skola": url, "jmeno": username, "heslo": password}
    )


@pytest.fixture
def logged_in(client, school):
    response = do_login(client)
    assert response.status_code == 302, response.get_data(as_text=True)
    return client
