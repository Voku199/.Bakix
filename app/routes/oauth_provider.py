"""OAuth 2.0 provider endpoints — lets other apps offer "Přihlásit se přes Bakix".

Authorization Code flow with mandatory PKCE (S256) for confidential clients:

    GET  /oauth/authorize  → consent screen (needs a logged-in Bakix session)
    POST /oauth/authorize  → mints a single-use code, redirects back to the client
    POST /oauth/token      → server-to-server code exchange (client_secret + PKCE)
    GET  /oauth/userinfo   → profile for a Bearer token (sub, name, school, tier)

Scopes: "profile" (always required) shares only the profile. A client that
also needs the user's Bakaláře tokens must request "bakalare" — the consent
screen then says so explicitly, and userinfo includes the tokens only for
tokens minted with that scope. Bakaláře passwords never leave this app.
Storage details live in app/database/oauth_db.py.
"""

import base64
import hashlib
import hmac
import logging
from urllib.parse import quote, urlencode

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from app.database import oauth_db
from app.database.db import fetch_row, get_subscription_tier
from app.extensions import limiter

log = logging.getLogger(__name__)

oauth_bp = Blueprint("oauth", __name__)

# "profile" is the mandatory base scope; "bakalare" additionally shares the
# user's Bakaláře access+refresh tokens and is called out on the consent screen.
_ALLOWED_SCOPES = frozenset({"profile", "bakalare"})


def _normalize_scope(raw: str) -> str | None:
    """Canonical scope string ("profile" / "bakalare profile"), or None when
    the request asks for an unknown scope or omits "profile"."""
    requested = set((raw or "profile").split())
    if "profile" not in requested or requested - _ALLOWED_SCOPES:
        return None
    return " ".join(sorted(requested))


def _redirect_back(redirect_uri: str, **params) -> "Response":
    """Redirect to an already-validated client redirect_uri with query params."""
    sep = "&" if "?" in redirect_uri else "?"
    return redirect(f"{redirect_uri}{sep}{urlencode(params)}")


def _validated_request():
    """Validate the authorize params shared by GET (consent) and POST (decision).

    Returns (client, params, error_response). Invalid client/redirect_uri must
    NOT redirect (that would be an open redirect) — it renders an error page.
    """
    values = request.values
    client_id = values.get("client_id", "")
    redirect_uri = values.get("redirect_uri", "")

    client = oauth_db.get_client(client_id)
    if not client or redirect_uri not in client["redirect_uris"]:
        log.warning("oauth: authorize with unknown client/redirect_uri client_id=%r", client_id)
        return None, None, (render_template(
            "oauth_error.html",
            message="Neplatná aplikace nebo návratová adresa.",
        ), 400)

    scope = _normalize_scope(values.get("scope", ""))

    params = {
        "response_type": values.get("response_type", ""),
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope or "",
        "state": values.get("state", ""),
        "code_challenge": values.get("code_challenge", ""),
        "code_challenge_method": values.get("code_challenge_method", ""),
    }

    if scope is None:
        return None, None, _redirect_back(
            redirect_uri, error="invalid_scope", state=params["state"])

    if (params["response_type"] != "code"
            or not params["state"]
            or not params["code_challenge"]
            or params["code_challenge_method"] != "S256"):
        return None, None, _redirect_back(
            redirect_uri, error="invalid_request", state=params["state"])

    return client, params, None


@oauth_bp.route("/oauth/authorize", methods=["GET", "POST"])
@limiter.limit("30 per minute")
def authorize():
    client, params, error = _validated_request()
    if error is not None:
        return error

    if not session.get("user_id"):
        # Send through the normal login and come straight back here.
        return redirect(url_for("login.login") + "?" + urlencode(
            {"next": request.full_path if request.method == "GET"
             else "/oauth/authorize?" + urlencode(params)}))

    if session.get("is_demo"):
        return render_template(
            "oauth_error.html",
            message="Demo účet nelze použít pro přihlášení do jiné aplikace.",
        ), 403

    if request.method == "GET":
        return render_template("oauth_consent.html", client_name=client["name"],
                               params=params,
                               wants_bakalare="bakalare" in params["scope"].split())

    # POST — the consent decision (CSRF-protected form).
    if request.form.get("decision") != "allow":
        return _redirect_back(params["redirect_uri"],
                              error="access_denied", state=params["state"])

    code = oauth_db.store_code(
        client_id=client["client_id"],
        user_id=session["user_id"],
        redirect_uri=params["redirect_uri"],
        code_challenge=params["code_challenge"],
        scope=params["scope"],
    )
    log.info("oauth: code issued client=%s user=%.8s", client["client_id"], session["user_id"])
    return _redirect_back(params["redirect_uri"], code=code, state=params["state"])


@oauth_bp.route("/oauth/token", methods=["POST"])
@limiter.limit("10 per minute")
def token():
    if request.form.get("grant_type") != "authorization_code":
        return jsonify({"error": "unsupported_grant_type"}), 400

    client = oauth_db.get_client(request.form.get("client_id", ""))
    if not client or not oauth_db.verify_client_secret(
            client, request.form.get("client_secret", "")):
        return jsonify({"error": "invalid_client"}), 401

    code_row = oauth_db.consume_code(request.form.get("code", ""), client["client_id"])
    if not code_row:
        return jsonify({"error": "invalid_grant"}), 400

    if code_row["redirect_uri"] != request.form.get("redirect_uri", ""):
        return jsonify({"error": "invalid_grant"}), 400

    verifier = request.form.get("code_verifier", "")
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    if not hmac.compare_digest(expected, code_row["code_challenge"]):
        return jsonify({"error": "invalid_grant"}), 400

    access_token = oauth_db.issue_token(
        client["client_id"], code_row["user_id"], code_row["scope"],
        code_row["code_hash"])
    log.info("oauth: token issued client=%s user=%.8s",
             client["client_id"], code_row["user_id"])
    return jsonify({
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": oauth_db.TOKEN_TTL_SECONDS,
        "scope": code_row["scope"],
    })


@oauth_bp.route("/oauth/userinfo")
@limiter.limit("30 per minute")
def userinfo():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return jsonify({"error": "invalid_token"}), 401

    token_row = oauth_db.lookup_token(auth[len("Bearer "):].strip())
    if not token_row:
        return jsonify({"error": "invalid_token"}), 401

    user_id = token_row["user_id"]
    row = fetch_row(user_id) or {}
    profile = {
        "sub": user_id,
        "display_name": row.get("display_name") or "",
        "school_url": row.get("school_url") or "",
        "subscription_tier": get_subscription_tier(user_id),
    }
    # Bakaláře tokens are shared only when the user consented to the
    # "bakalare" scope — the consent screen names it explicitly.
    if "bakalare" in (token_row["scope"] or "").split():
        profile["bakalare_access_token"] = ""
        profile["bakalare_refresh_token"] = ""
        # The stored access_token column can be NULL (accounts created before the
        # column existed, or a login where upsert_all failed) or expired. Handing
        # that to the client makes it think Bakaláře isn't linked. get_token
        # transparently refreshes / re-logs in from the stored encrypted
        # credentials, so the client always receives a usable token. Re-read the
        # row afterwards to pick up the refresh_token get_token may have rotated.
        school_url = row.get("school_url") or ""
        if school_url:
            from app.services.bakalari import BakalariService
            access_token = BakalariService(base_url=school_url).get_token(user_id)
            if access_token:
                fresh = fetch_row(user_id) or row
                profile["bakalare_access_token"] = access_token
                profile["bakalare_refresh_token"] = fresh.get("refresh_token") or ""
    resp = jsonify(profile)
    resp.headers["Cache-Control"] = "no-store"
    return resp
