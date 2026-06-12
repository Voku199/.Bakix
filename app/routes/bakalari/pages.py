"""AI-generated study pages: listing, rendering, CRUD, regenerate, modify."""

import logging

from flask import render_template, session, redirect, url_for, request, jsonify, abort
from flask_babel import gettext as _
from markupsafe import Markup

from app.extensions import limiter
from app.routes.bakalari import bakalari_bp
from app.routes.bakalari.ai_chat import _resolve_conversation
from app.routes.bakalari.helpers import (
    _get_svc_and_token, _sanitize_html, get_user_projects,
)
from app.services.gemini_service import GeminiService, RateLimitedError

log = logging.getLogger(__name__)

@bakalari_bp.route("/api/ai/pages", methods=["GET"])
@limiter.limit("30 per minute")
def api_ai_pages():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    return jsonify(get_user_projects(user_id))



@bakalari_bp.route("/api/ai/generated/<page_id>", methods=["GET"])
@limiter.limit("30 per minute")
def api_ai_generated(page_id):
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("welcome"))

    # Strict validation: uuid4().hex is 32 lowercase hex chars
    if not page_id.isalnum() or len(page_id) > 32:
        return abort(404)

    from app.database.db import get_generated_page
    page = get_generated_page(page_id)
    if not page or page["user_id"] != user_id:
        return abort(404)

    return render_template(
        "generated_page.html",
        title=page["title"] or "AI obsah",
        content=Markup(_sanitize_html(page["html"])),
        page_id=page_id,
    )


@bakalari_bp.route("/api/ai/generated/<page_id>", methods=["PUT"])
@limiter.limit("30 per minute")
def api_ai_generated_update(page_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    if not page_id.isalnum() or len(page_id) > 32:
        return abort(404)

    body    = request.get_json(force=True, silent=True) or {}
    content = (body.get("content") or "").strip()
    if not content:
        return jsonify({"error": _("Prázdný obsah")}), 400

    from app.database.db import update_generated_page_html
    if not update_generated_page_html(page_id, user_id, content):
        return abort(404)
    return jsonify({"ok": True})


@bakalari_bp.route("/api/ai/generated/<page_id>", methods=["DELETE"])
@limiter.limit("30 per minute")
def api_ai_generated_delete(page_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    if not page_id.isalnum() or len(page_id) > 32:
        return abort(404)

    from app.database.db import delete_generated_page
    if not delete_generated_page(page_id, user_id):
        return abort(404)
    return jsonify({"ok": True})


@bakalari_bp.route("/api/ai/regen/<page_id>", methods=["POST"])
@limiter.limit("20 per minute")
def api_ai_regen(page_id):
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Not authenticated"}), 401

        if not page_id.isalnum() or len(page_id) > 32:
            return abort(404)

        from app.database.db import get_generated_page, update_generated_page_html
        page = get_generated_page(page_id)
        if not page or page["user_id"] != user_id:
            return abort(404)
        current_html = page["html"]

        body   = request.get_json(force=True, silent=True) or {}
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            return jsonify({"error": _("Prázdný požadavek")}), 400

        student_data = None
        try:
            svc, token, _uid = _get_svc_and_token()
            if token:
                marks_raw = svc.get_marks(token)
                if isinstance(marks_raw, dict) and "Subjects" in marks_raw:
                    student_data = {
                        "subjects": [
                            {"name": (s.get("Subject") or {}).get("Name"), "average": s.get("AverageText")}
                            for s in marks_raw["Subjects"] or []
                        ]
                    }
        except Exception:
            pass

        try:
            new_html = GeminiService().regenerate_page(current_html, prompt, student_data, user_id=user_id)
        except RateLimitedError as exc:
            return jsonify({"ok": False, "error": "rate_limited", "tier": exc.tier}), 429
        except ValueError:
            return jsonify({"error": "GEMINI_API_KEY not configured"}), 503

        update_generated_page_html(page_id, user_id, new_html)
        return jsonify({"ok": True})
    except Exception:
        log.exception("api_ai_regen: unexpected error")
        return jsonify({"error": _("Interní chyba serveru")}), 500


@bakalari_bp.route("/api/ai/modify/<page_id>", methods=["POST"])
@limiter.limit("20 per minute")
def api_ai_modify(page_id):
    """Stateful page modification — uses persistent conversation history per user.

    Accepts {"prompt": "..."} and rewrites the stored page HTML using Gemini
    with the full conversation context, so accumulated instructions (e.g.
    'modern design', 'focus on Matematika') carry forward across requests.
    """
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Not authenticated"}), 401

        if not page_id.isalnum() or len(page_id) > 32:
            return abort(404)

        from app.database.db import get_generated_page, update_generated_page_html
        page = get_generated_page(page_id)
        if not page or page["user_id"] != user_id:
            return abort(404)

        body   = request.get_json(force=True, silent=True) or {}
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            return jsonify({"error": _("Prázdný požadavek")}), 400

        current_html = page["html"]
        conversation_id = _resolve_conversation(user_id, body.get("conversation_id"))

        try:
            new_html = GeminiService().modify_page(user_id, conversation_id, current_html, prompt)
        except RateLimitedError as exc:
            return jsonify({"ok": False, "error": "rate_limited", "tier": exc.tier}), 429
        except ValueError:
            return jsonify({"error": "GEMINI_API_KEY not configured"}), 503

        update_generated_page_html(page_id, user_id, new_html)

        return jsonify({"ok": True, "page_url": f"/api/ai/generated/{page_id}"})
    except Exception:
        log.exception("api_ai_modify: unexpected error")
        return jsonify({"error": _("Interní chyba serveru")}), 500

