"""AI assistant endpoints: insights, chat, and multi-conversation management."""

import datetime
import json
import logging
import uuid

from flask import session, request, jsonify, abort
from flask_babel import gettext as _

from app.extensions import limiter
from app.routes.bakalari import bakalari_bp
from app.routes.bakalari.helpers import (
    _FREE_MAX_CHATS, _FREE_MAX_PAGES, _get_svc_and_token, _prep_chat_msg,
)
from app.services.gemini_service import GeminiService, has_pending_skill, is_valid_model, resolve_model_for_tier, AI_MODE_NORMAL, AI_MODE_THINKING

log = logging.getLogger(__name__)

@bakalari_bp.route("/api/gemini/insights", methods=["GET"])
@limiter.limit("10 per minute")
def api_gemini_insights():
    try:
        svc, token, user_id = _get_svc_and_token()
        if not token:
            return jsonify({"error": "Not authenticated"}), 401

        marks_data = svc.get_marks(token)
        if marks_data.get("status_code") == 401:
            token = svc.reauth(user_id)
            if not token:
                return jsonify({"error": "Not authenticated"}), 401
            marks_data = svc.get_marks(token)
            if marks_data.get("status_code") == 401:
                return jsonify({"error": "Not authenticated"}), 401

        today   = datetime.date.today()
        to_date = today + datetime.timedelta(days=7)
        hw_data = svc.get_homeworks(token, today.isoformat(), to_date.isoformat())
        if hw_data.get("status_code") == 401:
            hw_data = {}

        subjects_summary = [
            {
                "subject": (s.get("Subject") or {}).get("Name"),
                "average": s.get("AverageText"),
                "marks":   [m.get("MarkText") for m in (s.get("Marks") or [])],
            }
            for s in (marks_data.get("Subjects") if isinstance(marks_data, dict) else []) or []
        ]
        homeworks_summary = [
            {
                "subject": (hw.get("Subject") or {}).get("Name"),
                "content": (hw.get("Content") or "")[:80],
                "due":     hw.get("DateEnd"),
            }
            for hw in (hw_data.get("Homeworks") if isinstance(hw_data, dict) else []) or []
            if not hw.get("Closed") and not hw.get("Done")
        ]

        payload = {"marks": subjects_summary, "upcoming_homeworks": homeworks_summary}

        try:
            gemini = GeminiService()
            result = gemini.get_proactive_insights(payload)
        except ValueError:
            return jsonify({"error": "GEMINI_API_KEY not configured"}), 503

        return jsonify(result)
    except Exception:
        log.exception("api_gemini_insights: unexpected error")
        return jsonify({"error": _("Interní chyba serveru")}), 500


@bakalari_bp.route("/api/gemini/chat", methods=["POST"])
@limiter.limit("20 per minute")
def api_gemini_chat():
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Not authenticated"}), 401

        body    = request.get_json(force=True, silent=True) or {}
        message = (body.get("message") or "").strip()
        history = body.get("history") or []

        if not message:
            return jsonify({"error": _("Prázdná zpráva")}), 400

        try:
            gemini = GeminiService()
            reply  = gemini.send_chat_message(history, message)
        except ValueError:
            return jsonify({"error": "GEMINI_API_KEY not configured"}), 503

        return jsonify({"reply": reply})
    except Exception:
        log.exception("api_gemini_chat: unexpected error")
        return jsonify({"error": _("Interní chyba serveru")}), 500



# ── Conversations (multiple chats per user) ─────────────────────────────────

def _resolve_conversation(user_id: str, raw_id) -> str:
    """Return a valid conversation id owned by user_id, creating one if needed."""
    from app.database.db import get_conversation, create_conversation
    raw_id = (raw_id or "").strip()
    if raw_id and get_conversation(raw_id, user_id):
        return raw_id
    return create_conversation(user_id)


@bakalari_bp.route("/api/ai/conversations", methods=["GET"])
@limiter.limit("30 per minute")
def api_conversations_list():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    from app.database.db import list_conversations
    return jsonify(list_conversations(user_id))


@bakalari_bp.route("/api/ai/conversations", methods=["POST"])
@limiter.limit("20 per minute")
def api_conversations_create():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    from app.database.db import create_conversation, count_conversations, get_subscription_tier
    if get_subscription_tier(user_id) != "premium" and count_conversations(user_id) >= _FREE_MAX_CHATS:
        return jsonify({
            "error": "chat_limit",
            "message": f"Ve free verzi můžeš mít {_FREE_MAX_CHATS} chaty. Smaž některý, nebo přejdi na Premium pro neomezené chaty. ✦",
            "tier": "free",
        }), 403
    body  = request.get_json(force=True, silent=True) or {}
    title = (body.get("title") or "Nový chat").strip()[:80] or "Nový chat"
    conv_id = create_conversation(user_id, title)
    return jsonify({"id": conv_id, "title": title})


@bakalari_bp.route("/api/ai/conversations/<conversation_id>", methods=["PATCH"])
@limiter.limit("30 per minute")
def api_conversations_rename(conversation_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    from app.database.db import rename_conversation
    body  = request.get_json(force=True, silent=True) or {}
    title = (body.get("title") or "").strip()[:80]
    if not title:
        return jsonify({"error": _("Chybí název")}), 400
    if not rename_conversation(conversation_id, user_id, title):
        return abort(404)
    return jsonify({"ok": True, "title": title})


@bakalari_bp.route("/api/ai/conversations/<conversation_id>", methods=["DELETE"])
@limiter.limit("30 per minute")
def api_conversations_delete(conversation_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    from app.database.db import delete_conversation
    if not delete_conversation(conversation_id, user_id):
        return abort(404)
    return jsonify({"ok": True})


@bakalari_bp.route("/api/ai/conversations/<conversation_id>/messages", methods=["GET"])
@limiter.limit("30 per minute")
def api_conversations_messages(conversation_id):
    """Return the rendered messages of one conversation for rehydrating the thread."""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    from app.database.db import get_conversation_history_rows
    rows = get_conversation_history_rows(conversation_id, user_id)
    if rows is None:
        return abort(404)

    out = []
    for r in rows:
        role = r["role"]
        if role == "user":
            out.append({"role": "user", "message": r["content"], "is_html": False,
                        "timestamp": r["timestamp"]})
            continue
        # model rows store the full ai_result JSON (or a "[page modified: …]" note)
        text = r["content"]
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and "message" in parsed:
                text = parsed.get("message") or ""
        except (ValueError, TypeError):
            pass
        if not text:
            continue
        msg, is_html = _prep_chat_msg(text)
        out.append({"role": "model", "message": msg, "is_html": is_html,
                    "timestamp": r["timestamp"]})
    return jsonify({"id": conversation_id, "messages": out})


# ── AI chat endpoint (structured response with optional page generation) ─────

@bakalari_bp.route("/api/ai/chat", methods=["POST"])
@limiter.limit("30 per minute")
def api_ai_chat():
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Not authenticated"}), 401

        body      = request.get_json(force=True, silent=True) or {}
        message   = (body.get("message") or "").strip()
        chat_mode = (body.get("chat_mode") or "auto").strip().lower()
        _raw_model = (body.get("model_id") or "").strip()
        model_id  = _raw_model if is_valid_model(_raw_model) else None
        ai_mode   = AI_MODE_THINKING if (body.get("ai_mode") or "") == AI_MODE_THINKING else AI_MODE_NORMAL

        if not message:
            return jsonify({"error": _("Prázdná zpráva")}), 400

        # ── Premium gating ────────────────────────────────────────────────────
        from app.database.db import (
            get_subscription_tier, get_conversation, create_conversation,
            count_conversations, count_generated_pages, set_conversation_title_if_default,
        )
        tier = get_subscription_tier(user_id)
        # #1 Thinking mode is Premium-only — silently fall back to Normal for free.
        thinking_locked = False
        if ai_mode == AI_MODE_THINKING and tier != "premium":
            ai_mode = AI_MODE_NORMAL
            thinking_locked = True
        # #2 Pro models are Premium-only — free users get the freemium default.
        model_id, model_locked = resolve_model_for_tier(model_id, tier)

        # ── Resolve / create the conversation (with the free chat cap, #4) ────
        conversation_id = (body.get("conversation_id") or "").strip()
        if conversation_id and not get_conversation(conversation_id, user_id):
            conversation_id = ""
        if not conversation_id:
            if tier != "premium" and count_conversations(user_id) >= _FREE_MAX_CHATS:
                return jsonify({
                    "error": "chat_limit",
                    "message": f"Ve free verzi můžeš mít {_FREE_MAX_CHATS} chaty. "
                               "Smaž některý v 🗂, nebo přejdi na Premium pro neomezené chaty. ✦",
                    "tier": "free",
                }), 403
            conversation_id = create_conversation(user_id)
        # Name a still-unnamed chat after its first message.
        set_conversation_title_if_default(conversation_id, message)

        _msg_lower = message.lower().strip()

        # /studie plan — personalised study plan
        if _msg_lower in ("/studie plan", "/studie plán", "/studijní plán", "/studijni plan"):
            try:
                from app.services.weekly_summary import generate_study_plan_for_user
                _result = generate_study_plan_for_user(user_id)
            except Exception:
                log.exception("api_ai_chat: study plan command failed")
                _result = None

            if _result is None:
                _plan_msg = "Studijní plán se nepodařilo vygenerovat. Zkontroluj připojení k Bakalářům."
            else:
                _parts = [_result.get("plan", "")]
                if _result.get("priority_tasks"):
                    _parts.append("**Prioritní úkoly:**\n" + "\n".join(f"• {t}" for t in _result["priority_tasks"]))
                if _result.get("study_slots"):
                    _parts.append(f"**Studijní okna:** {_result['study_slots']}")
                if _result.get("tip"):
                    _parts.append(f"**Tip:** {_result['tip']}")
                _plan_msg = "\n\n".join(p for p in _parts if p)

            _plan_msg, _plan_html = _prep_chat_msg(_plan_msg)
            return jsonify({
                "conversation_id": conversation_id,
                "message":      _plan_msg,
                "is_html":      _plan_html,
                "action_url":   None,
                "action_label": None,
                "is_test":      False,
                "sender":       "ai",
                "timestamp":    datetime.datetime.utcnow().isoformat() + "Z",
            })

        # "Vysvětlit přes AI:" — contextual text explanation from chat selection
        if _msg_lower.startswith("vysvětlit přes ai:"):
            _explain_term = message[len("Vysvětlit přes AI:"):].strip()
            if _explain_term:
                try:
                    ai_result = GeminiService().explain_term(user_id, conversation_id, _explain_term, model_id=model_id, ai_mode=ai_mode)
                except ValueError:
                    return jsonify({"error": "GEMINI_API_KEY not configured"}), 503
                _exp_msg, _exp_html = _prep_chat_msg(ai_result.get("message", ""))
                return jsonify({
                    "conversation_id": conversation_id,
                    "message":      _exp_msg,
                    "is_html":      _exp_html,
                    "action_url":   None,
                    "action_label": None,
                    "is_test":      False,
                    "sender":       "ai",
                    "timestamp":    datetime.datetime.utcnow().isoformat() + "Z",
                })

        # /shrnutí commands — short-circuit to period summaries
        if _msg_lower in ("/shrnutí den", "/shrnuti den", "/shrnutí", "/shrnuti"):
            _is_daily = "den" in _msg_lower
            try:
                if _is_daily:
                    from app.services.weekly_summary import generate_daily_summary_for_user
                    _result = generate_daily_summary_for_user(user_id)
                else:
                    from app.services.weekly_summary import generate_weekly_summary_for_user
                    _result = generate_weekly_summary_for_user(user_id)
            except Exception:
                log.exception("api_ai_chat: summary command failed")
                _result = None

            if _result is None:
                _summary_msg = "Shrnutí se nepodařilo vygenerovat. Zkontroluj připojení k Bakalářům."
            else:
                _parts = [_result.get("summary", "")]
                if _result.get("weak_subjects"):
                    _parts.append("**Slabá místa:** " + ", ".join(_result["weak_subjects"]))
                if _result.get("study_plan"):
                    _label = "Tip na dnešní večer" if _is_daily else "Plán na příští týden"
                    _parts.append(f"**{_label}:** {_result['study_plan']}")
                if _result.get("cta"):
                    _parts.append(_result["cta"])
                _summary_msg = "\n\n".join(p for p in _parts if p)

            _summary_msg, _summary_html = _prep_chat_msg(_summary_msg)
            return jsonify({
                "conversation_id": conversation_id,
                "message":      _summary_msg,
                "is_html":      _summary_html,
                "action_url":   None,
                "action_label": None,
                "is_test":      False,
                "sender":       "ai",
                "timestamp":    datetime.datetime.utcnow().isoformat() + "Z",
            })

        # /skill command or active skill-creation questionnaire — short-circuit normal flow
        if message.startswith("/skill") or has_pending_skill(user_id):
            try:
                ai_result = GeminiService().handle_skill_command(user_id, message)
            except ValueError:
                return jsonify({"error": "GEMINI_API_KEY not configured"}), 503
            _skill_msg, _skill_html = _prep_chat_msg(ai_result.get("message", ""))
            return jsonify({
                "conversation_id": conversation_id,
                "message":      _skill_msg,
                "is_html":      _skill_html,
                "action_url":   None,
                "action_label": None,
                "is_test":      False,
                "sender":       "ai",
                "timestamp":    datetime.datetime.utcnow().isoformat() + "Z",
            })

        # Best-effort: fetch student marks for grade-context routing.
        # Skipped when chat_mode == "general" to avoid unnecessary API calls.
        student_data = None
        flat_grades: list = []
        if chat_mode != "general":
            try:
                svc, token, _uid = _get_svc_and_token()
                if token:
                    marks_raw = svc.get_marks(token)
                    if isinstance(marks_raw, dict) and "Subjects" in marks_raw:
                        student_data = {
                            "subjects": [
                                {
                                    "name":    (s.get("Subject") or {}).get("Name"),
                                    "average": s.get("AverageText"),
                                }
                                for s in marks_raw["Subjects"] or []
                            ]
                        }
                        for s in marks_raw["Subjects"] or []:
                            subject_name = (s.get("Subject") or {}).get("Name", "")
                            for m in s.get("Marks") or []:
                                flat_grades.append({
                                    "subject":   subject_name,
                                    "MarkText":  m.get("MarkText"),
                                    "Caption":   m.get("Caption") or "",
                                    "EditDate":  m.get("EditDate") or "",
                                    "timestamp": m.get("EditDate") or "",
                                    "topic":     m.get("Caption") or "",
                                })
            except Exception:
                pass

        try:
            gemini = GeminiService()
            if chat_mode == "grades" or (chat_mode == "auto" and flat_grades):
                ai_result = gemini.handle_grades_context(user_id, conversation_id, flat_grades, message, model_id=model_id, ai_mode=ai_mode)
            else:
                ai_result = gemini.get_response(user_id, conversation_id, message, student_data, model_id=model_id, ai_mode=ai_mode)
        except ValueError:
            return jsonify({"error": "GEMINI_API_KEY not configured"}), 503

        action_url = None
        page_limit_reached = False
        html_body  = ai_result.get("page_content_html") or ""
        if ai_result.get("intent") == "create_page" and html_body.strip():
            # #3 Free users keep at most _FREE_MAX_PAGES saved study pages.
            if tier != "premium" and count_generated_pages(user_id) >= _FREE_MAX_PAGES:
                page_limit_reached = True
            else:
                from app.database.db import create_generated_page
                page_id = uuid.uuid4().hex
                create_generated_page(
                    page_id, user_id, ai_result.get("page_title") or "AI obsah", html_body,
                )
                action_url = f"/api/ai/generated/{page_id}"

        chat_msg, is_html = _prep_chat_msg(ai_result.get("message", ""))
        if page_limit_reached:
            chat_msg += (
                f"\n\n_(Dosáhl jsi limitu {_FREE_MAX_PAGES} uložených stránek ve free verzi. "
                "Smaž některou přes ✦, nebo přejdi na Premium pro neomezené stránky.)_"
            )
        resp = {
            "conversation_id": conversation_id,
            "message":      chat_msg,
            "is_html":      is_html,
            "action_url":   action_url,
            "action_label": ai_result.get("action_label") if action_url else None,
            "is_test":      bool(ai_result.get("is_test", False)),
            "sender":       "ai",
            "timestamp":    datetime.datetime.utcnow().isoformat() + "Z",
        }
        if thinking_locked:    resp["thinking_locked"] = True
        if model_locked:       resp["model_locked"] = True
        if page_limit_reached: resp["page_limit_reached"] = True
        if ai_result.get("rate_limited"):
            resp["rate_limited"] = True
            resp["tier"]         = ai_result.get("tier", "free")
        return jsonify(resp)
    except Exception:
        log.exception("api_ai_chat: unexpected error")
        return jsonify({"error": _("Interní chyba serveru")}), 500

