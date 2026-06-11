"""Tests for the OAuth 2.0 provider ("Přihlásit se přes Bakix").

Covers:
  1. /oauth/authorize  — client/redirect_uri validation, PKCE S256 enforcement,
                         login redirect, demo-account block, consent decision
  2. /oauth/token      — client_secret check, PKCE verifier check, single-use
                         codes, replay detection with token revocation
  3. /oauth/userinfo   — Bearer auth, profile shape, nothing sensitive leaks
"""

import base64
import hashlib
import os
import secrets
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Isolated DB + fixed key, set before any app import reads them.
os.environ["BAKIX_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "oauth-test.db")
os.environ.setdefault("SECRET_KEY", "test-oauth-secret")

REDIRECT_URI = "http://client.example/cb"
TEST_USER = hashlib.sha256(b"https://test.example:student").hexdigest()


def _pkce_pair():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


@pytest.fixture(scope="module")
def app():
    from unittest.mock import patch
    with patch("app.services.scheduler.start_scheduler"):
        from app import create_app
        application = create_app()
    application.config["TESTING"] = True
    application.config["WTF_CSRF_ENABLED"] = False  # consent POST in tests
    from app.database.connection import get_connection
    with get_connection() as db:
        db.execute(
            "INSERT OR REPLACE INTO saved_credentials "
            "(user_id, school_url, enc_creds, display_name) VALUES (?, ?, ?, ?)",
            (TEST_USER, "https://test.example", "dummy", "Test Student"),
        )
    return application


@pytest.fixture(scope="module")
def oauth_client(app):
    from app.database.oauth_db import create_client
    client_id, client_secret = create_client("TestApp", [REDIRECT_URI])
    return {"client_id": client_id, "client_secret": client_secret}


@pytest.fixture()
def browser(app):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = TEST_USER
    return client


def _authorize_params(oauth_client, challenge, **over):
    return {
        "response_type": "code",
        "client_id": oauth_client["client_id"],
        "redirect_uri": REDIRECT_URI,
        "scope": "profile",
        "state": "test-state",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **over,
    }


def _get_code(browser, oauth_client):
    verifier, challenge = _pkce_pair()
    params = _authorize_params(oauth_client, challenge)
    resp = browser.post("/oauth/authorize", data={**params, "decision": "allow"})
    assert resp.status_code == 302
    query = parse_qs(urlparse(resp.headers["Location"]).query)
    return query["code"][0], verifier


def _token_request(browser, oauth_client, code, verifier, **over):
    return browser.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": oauth_client["client_id"],
        "client_secret": oauth_client["client_secret"],
        "code_verifier": verifier,
        **over,
    })


# ── /oauth/authorize ───────────────────────────────────────────────────────────

def test_unknown_client_renders_error_no_redirect(browser, oauth_client):
    _, challenge = _pkce_pair()
    resp = browser.get("/oauth/authorize", query_string=_authorize_params(
        oauth_client, challenge, client_id="nonexistent"))
    assert resp.status_code == 400


def test_unregistered_redirect_uri_renders_error_no_redirect(browser, oauth_client):
    _, challenge = _pkce_pair()
    resp = browser.get("/oauth/authorize", query_string=_authorize_params(
        oauth_client, challenge, redirect_uri="https://evil.example/cb"))
    assert resp.status_code == 400


def test_plain_pkce_rejected(browser, oauth_client):
    _, challenge = _pkce_pair()
    resp = browser.get("/oauth/authorize", query_string=_authorize_params(
        oauth_client, challenge, code_challenge_method="plain"))
    assert resp.status_code == 302
    assert "error=invalid_request" in resp.headers["Location"]


def test_missing_state_rejected(browser, oauth_client):
    _, challenge = _pkce_pair()
    resp = browser.get("/oauth/authorize", query_string=_authorize_params(
        oauth_client, challenge, state=""))
    assert resp.status_code == 302
    assert "error=invalid_request" in resp.headers["Location"]


def test_anonymous_user_sent_to_login_with_next(app, oauth_client):
    _, challenge = _pkce_pair()
    resp = app.test_client().get(
        "/oauth/authorize", query_string=_authorize_params(oauth_client, challenge))
    assert resp.status_code == 302
    assert "/login?next=%2Foauth%2Fauthorize" in resp.headers["Location"]


def test_demo_account_cannot_authorize(app, oauth_client):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = "demo"
        s["is_demo"] = True
    _, challenge = _pkce_pair()
    resp = client.get("/oauth/authorize",
                      query_string=_authorize_params(oauth_client, challenge))
    assert resp.status_code == 403


def test_consent_screen_shows_client_name(browser, oauth_client):
    _, challenge = _pkce_pair()
    resp = browser.get("/oauth/authorize",
                       query_string=_authorize_params(oauth_client, challenge))
    assert resp.status_code == 200
    assert b"TestApp" in resp.data


def test_deny_redirects_with_access_denied(browser, oauth_client):
    _, challenge = _pkce_pair()
    params = _authorize_params(oauth_client, challenge)
    resp = browser.post("/oauth/authorize", data={**params, "decision": "deny"})
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert location.startswith(REDIRECT_URI)
    assert "error=access_denied" in location and "state=test-state" in location


def test_allow_redirects_with_code_and_state(browser, oauth_client):
    code, _ = _get_code(browser, oauth_client)
    assert code


# ── /oauth/token ───────────────────────────────────────────────────────────────

def test_wrong_client_secret_rejected(browser, oauth_client):
    code, verifier = _get_code(browser, oauth_client)
    resp = _token_request(browser, oauth_client, code, verifier,
                          client_secret="wrong")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "invalid_client"


def test_wrong_verifier_rejected(browser, oauth_client):
    code, _ = _get_code(browser, oauth_client)
    resp = _token_request(browser, oauth_client, code, "wrong-verifier" * 4)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_grant"


def test_wrong_redirect_uri_rejected(browser, oauth_client):
    code, verifier = _get_code(browser, oauth_client)
    resp = _token_request(browser, oauth_client, code, verifier,
                          redirect_uri="http://client.example/other")
    assert resp.status_code == 400


def test_valid_exchange_and_userinfo(browser, oauth_client):
    code, verifier = _get_code(browser, oauth_client)
    resp = _token_request(browser, oauth_client, code, verifier)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["token_type"] == "Bearer" and body["access_token"]

    info = browser.get("/oauth/userinfo", headers={
        "Authorization": f"Bearer {body['access_token']}"})
    assert info.status_code == 200
    profile = info.get_json()
    assert profile["sub"] == TEST_USER
    assert profile["display_name"] == "Test Student"
    assert profile["subscription_tier"] in ("free", "premium")
    # credentials and Bakaláře tokens must never leak through userinfo
    for forbidden in ("enc_creds", "access_token", "refresh_token", "settings_json"):
        assert forbidden not in profile


def test_code_replay_revokes_tokens(browser, oauth_client):
    code, verifier = _get_code(browser, oauth_client)
    first = _token_request(browser, oauth_client, code, verifier)
    token = first.get_json()["access_token"]

    replay = _token_request(browser, oauth_client, code, verifier)
    assert replay.status_code == 400
    assert replay.get_json()["error"] == "invalid_grant"

    revoked = browser.get("/oauth/userinfo",
                          headers={"Authorization": f"Bearer {token}"})
    assert revoked.status_code == 401


def test_userinfo_rejects_garbage_token(browser):
    resp = browser.get("/oauth/userinfo",
                       headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401


def test_userinfo_requires_bearer(browser):
    assert browser.get("/oauth/userinfo").status_code == 401
