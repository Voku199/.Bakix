"""GeminiService — the AI assistant core.

Prompts live in ai_prompts.py, the model registry and rate-limit tiers in
ai_models.py, the OpenRouter fallback client in openrouter.py and skill
storage in skills_db.py. Public names from those modules are re-exported
here so existing imports keep working.
"""

import datetime
import hashlib
import json
import logging
import os
import re

from google import genai
from google.genai import types

from app.database.connection import get_connection
from app.services.ai_models import (  # noqa: F401 — re-exported
    AI_MODE_NORMAL, AI_MODE_THINKING, RateLimitedError,
    _DEFAULT_MODEL, _FREE_DEFAULT_MODEL, _FREE_MAX_SKILLS,
    _FREEMIUM_GEMINI_DAILY, _FREEMIUM_OR_HISTORY, _FREEMIUM_OR_MAX_TOKENS,
    _MODELS, _error_response, _rate_limited_response, _resolve_provider,
    is_premium_model, is_valid_model, list_models, resolve_model_for_tier,
)
from app.services.ai_prompts import (
    _AI_CHAT_PROMPT, _CHAT_PROMPT, _CONFIRMATION_KEYWORDS,
    _CONFIRMATION_SENTINEL, _DAILY_SUMMARY_PROMPT, _EXPLAIN_PROMPT,
    _GRADE_KEYWORDS, _HISTORY_CONTEXT, _HISTORY_KEEP, _INSIGHTS_PROMPT,
    _LIVE_PAGE_PROMPT, _MODIFICATION_KEYWORDS, _MODIFY_PROMPT, _REGEN_PROMPT,
    _SKILL_REFINE_PROMPT, _STUDY_PLAN_PROMPT, _WEEKLY_SUMMARY_PROMPT,
    _detect_factual_request,
)
from app.services.openrouter import (
    _call_openrouter, _is_quota_error, _or_status, _si_to_str,
    _strip_degenerate_tail,
)
from app.services.skills_db import (  # noqa: F401 — re-exported
    _clear_pending_skill, _get_pending_skill, _get_skill, _list_skills,
    _save_skill, _delete_skill, _set_pending_skill, has_pending_skill,
)

log = logging.getLogger(__name__)

class GeminiService:
    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set")
        # Without a timeout a stalled Gemini call holds the request thread
        # indefinitely; the OpenRouter fallback already uses timeout=30 s.
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=60_000),  # ms
        )
        self._model = os.environ.get("GEMINI_MODEL")
        self._insights_config = types.GenerateContentConfig(
            system_instruction=_INSIGHTS_PROMPT,
            response_mime_type="application/json",
        )
        self._chat_config = types.GenerateContentConfig(
            system_instruction=_CHAT_PROMPT,
        )
        self._ai_chat_config = types.GenerateContentConfig(
            system_instruction=_AI_CHAT_PROMPT,
            response_mime_type="application/json",
        )
        self._live_page_config = types.GenerateContentConfig(
            system_instruction=_LIVE_PAGE_PROMPT,
            response_mime_type="application/json",
        )
        self._modify_config = types.GenerateContentConfig(
            system_instruction=_MODIFY_PROMPT,
        )

    # ── Gemini → OpenRouter fallback wrapper ─────────────────────────────────

    def _generate_with_fallback(
        self,
        config: types.GenerateContentConfig,
        contents: str,
        history: "list[dict] | None" = None,
        user_id: "str | None" = None,
        model_id: "str | None" = None,
        ai_mode: str = AI_MODE_NORMAL,
    ) -> str:
        """Call the appropriate AI backend based on model_id and rate-limit state.

        model_id — optional user-chosen model from _MODELS (None → _DEFAULT_MODEL).
        history  — raw list[{"role": "user"|"model", "text": str}] from DB.
        user_id  — when provided, enforces rate-limits before calling any API.
        """
        from app.database.db import log_ai_request

        effective_model = model_id or _DEFAULT_MODEL
        model_info = _MODELS.get(effective_model, _MODELS[_DEFAULT_MODEL])

        log.info("[AI REQUEST] user=%s model=%s group=%s", user_id[:8] if user_id else '?', effective_model, model_info['group'])

        # ── Freemium OpenRouter model: bypass Gemini budget, go direct ───────
        if model_info["group"] == "freemium" and model_info["provider"] == "openrouter":
            json_mode = getattr(config, "response_mime_type", "") == "application/json"
            # Thinking mode: full history + no token cap. Normal: trimmed + capped.
            if ai_mode == AI_MODE_THINKING:
                or_history   = history or []
                or_max_tokens = None
                log.info("[OR] mode=thinking — full history %s turns, unlimited tokens", len(or_history))
            else:
                or_history    = (history or [])[-_FREEMIUM_OR_HISTORY:]
                or_max_tokens = _FREEMIUM_OR_MAX_TOKENS
            try:
                result = _call_openrouter(
                    _si_to_str(config), contents, or_history, json_mode,
                    model=effective_model, max_tokens=or_max_tokens,
                )
            except Exception as _or_exc:
                if _or_status(_or_exc) == 402:
                    _tier = "free"
                    if user_id:
                        from app.database.db import get_subscription_tier
                        _tier = get_subscription_tier(user_id)
                    log.warning("OpenRouter 402 on freemium model %s", effective_model)
                    raise RateLimitedError(_tier, effective_model) from _or_exc
                raise
            if user_id:
                log_ai_request(user_id, "openrouter")
            return result

        # ── gemini-2.5-flash-lite: separate 10/day limit ─────────────────────
        if effective_model == "gemini-2.5-flash-lite" and user_id:
            from app.database.db import count_ai_requests, get_subscription_tier
            tier = get_subscription_tier(user_id)
            since_24h = (datetime.datetime.utcnow() - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            if count_ai_requests(user_id, "gemini-2.5-flash-lite", since_24h) >= _FREEMIUM_GEMINI_DAILY:
                raise RateLimitedError(tier, effective_model)
            log_ai_request(user_id, "gemini-2.5-flash-lite")

        elif user_id:
            # ── Rate-limit gate for Pro Gemini models ─────────────────────────
            provider, tier = _resolve_provider(user_id)
            if provider is None:
                raise RateLimitedError(tier)
            log_ai_request(user_id, "gemini")

        # ── Gemini API call (model_id selects which Gemini model) ─────────────
        gemini_model = effective_model if model_info["provider"] == "gemini" else (self._model or _DEFAULT_MODEL)
        try:
            if history:
                hist = [
                    types.Content(role=h["role"], parts=[types.Part(text=h["text"])])
                    for h in history
                    if h.get("role") in ("user", "model") and h.get("text")
                ]
                chat = self._client.chats.create(model=gemini_model, config=config, history=hist)
                return chat.send_message(contents).text
            return self._client.models.generate_content(
                model=gemini_model, config=config, contents=contents,
            ).text
        except Exception as exc:
            if not _is_quota_error(exc):
                raise
            log.warning("Gemini quota exceeded, routing to OpenRouter: %s", exc)
            json_mode = getattr(config, "response_mime_type", "") == "application/json"
            try:
                return _call_openrouter(_si_to_str(config), contents, history, json_mode)
            except Exception as _or_exc:
                if _or_status(_or_exc) == 402:
                    log.warning("OpenRouter 402 (no credits) on quota fallback")
                    _tier = "free"
                    if user_id:
                        from app.database.db import get_subscription_tier
                        _tier = get_subscription_tier(user_id)
                    raise RateLimitedError(_tier) from _or_exc
                raise

    # ── Persistent conversation history ───────────────────────────────────────

    def _load_history(self, user_id: str, conversation_id: "str | None" = None) -> "list[dict]":
        """Return the last _HISTORY_CONTEXT turns of one conversation.

        Scoped by conversation_id so each chat keeps its own context. Without an
        id there is no thread to load, so we return nothing.
        """
        if not conversation_id:
            return []
        try:
            with get_connection() as db:
                rows = db.execute(
                    "SELECT role, content FROM conversation_history "
                    "WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                    (conversation_id, _HISTORY_CONTEXT),
                ).fetchall()
            return [{"role": r["role"], "text": r["content"]} for r in reversed(rows)]
        except Exception:
            log.warning("_load_history failed for conv=%s", conversation_id)
            return []

    def _save_exchange(self, user_id: str, conversation_id: "str | None",
                       user_content: str, model_content: str) -> None:
        """Persist one user turn + one model turn in a conversation, then prune."""
        if not conversation_id:
            return
        try:
            with get_connection() as db:
                db.execute(
                    "INSERT INTO conversation_history (user_id, conversation_id, role, content) "
                    "VALUES (?, ?, 'user', ?)",
                    (user_id, conversation_id, user_content),
                )
                db.execute(
                    "INSERT INTO conversation_history (user_id, conversation_id, role, content) "
                    "VALUES (?, ?, 'model', ?)",
                    (user_id, conversation_id, model_content),
                )
                # Keep only the most recent _HISTORY_KEEP turns per conversation
                db.execute(
                    "DELETE FROM conversation_history WHERE conversation_id = ? AND id NOT IN "
                    "(SELECT id FROM conversation_history WHERE conversation_id = ? "
                    " ORDER BY id DESC LIMIT ?)",
                    (conversation_id, conversation_id, _HISTORY_KEEP),
                )
                # Bump the chat so it sorts to the top of the conversation list
                db.execute(
                    "UPDATE conversations SET updated_at = datetime('now') WHERE id = ?",
                    (conversation_id,),
                )
        except Exception:
            log.warning("_save_exchange failed for conv=%s", conversation_id)

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def _cache_hash(self, user_id: str, conversation_id: "str | None", prompt: str) -> str:
        # Include conversation_id so the same prompt in two chats doesn't collide.
        return hashlib.sha256((user_id + (conversation_id or "") + prompt).encode()).hexdigest()

    def get_cached_response(self, user_id: str, conversation_id: "str | None", prompt: str) -> "str | None":
        """Return a cached response string, or None on miss/stale.

        Key: SHA-256(user_id + conversation_id + prompt) — per-chat isolation.
        Confirmation prompts are never passed here (handled in handle_grades_context).
        """
        query_hash = self._cache_hash(user_id, conversation_id, prompt)
        try:
            with get_connection() as db:
                row = db.execute(
                    "SELECT response FROM gemini_cache "
                    "WHERE user_id = ? AND query_hash = ? "
                    "AND created_at > datetime('now', '-1 hour')",
                    (user_id, query_hash),
                ).fetchone()
            if row:
                log.debug("Gemini cache hit: user=%.8s hash=%.8s", user_id, query_hash)
                return row["response"]
        except Exception:
            log.warning("Gemini cache read failed for user=%.8s", user_id)
        return None

    def _save_to_cache(self, user_id: str, conversation_id: "str | None", prompt: str, response: str) -> None:
        query_hash = self._cache_hash(user_id, conversation_id, prompt)
        try:
            with get_connection() as db:
                db.execute(
                    "INSERT OR REPLACE INTO gemini_cache "
                    "(user_id, query_hash, response, created_at) "
                    "VALUES (?, ?, ?, datetime('now'))",
                    (user_id, query_hash, response),
                )
        except Exception:
            log.warning("Gemini cache write failed for user=%.8s", user_id)

    # ── Grade filtering ───────────────────────────────────────────────────────

    @staticmethod
    def filter_recent_grades(grades_list: list) -> list:
        """Return only grades from the last 30 days.

        Each dict must have 'timestamp' or 'EditDate' (ISO string, e.g. '2024-03-15T...').
        Items with an unparseable or missing date are excluded.
        """
        cutoff = datetime.date.today() - datetime.timedelta(days=30)
        result = []
        for grade in grades_list:
            raw_date = grade.get("timestamp") or grade.get("EditDate") or ""
            try:
                if datetime.date.fromisoformat(raw_date[:10]) >= cutoff:
                    result.append(grade)
            except ValueError:
                continue
        return result

    # ── Conversation-state detection ──────────────────────────────────────────

    @staticmethod
    def _is_modification_request(user_input: str, history: list) -> bool:
        """True when user is refining/modifying already-generated content."""
        lower = user_input.lower()
        if any(kw in lower for kw in _MODIFICATION_KEYWORDS):
            return True
        for entry in reversed(history[-6:]):
            if entry.get("role") == "model":
                text = entry.get("text", "")
                if '"create_page"' in text or "page_content_html" in text:
                    return True
        return False

    @staticmethod
    def _awaiting_grade_confirmation(history: list) -> bool:
        """True if the most recent model turn was the grade-confirmation prompt."""
        for entry in reversed(history):
            if entry.get("role") == "model":
                return _CONFIRMATION_SENTINEL in entry.get("text", "")
        return False

    @staticmethod
    def _is_grade_related(user_input: str) -> bool:
        lower = user_input.lower()
        return any(kw in lower for kw in _GRADE_KEYWORDS)

    @staticmethod
    def _wants_page_now(user_input: str) -> bool:
        lower = user_input.lower()
        return any(kw in lower for kw in _CONFIRMATION_KEYWORDS)

    # ── Core internal call ────────────────────────────────────────────────────

    def _call_api(
        self,
        user_id: str,
        conversation_id: "str | None",
        user_input: str,
        context_parts: "list[str]",
        config: types.GenerateContentConfig,
        model_id: "str | None" = None,
        ai_mode: str = AI_MODE_NORMAL,
    ) -> dict:
        """Load DB history → check cache → call API → save exchange → return dict.

        user_id is mandatory. context_parts are system/data lines prepended to
        the user message — they are NOT stored in conversation_history.
        model_id — optional user-chosen model from _MODELS.
        """
        # ── Proactive search for songs, lyrics, poems, quotes ────────────────
        should_search, proactive_query = _detect_factual_request(user_input)
        if should_search and not any("VÝSLEDKY VYHLEDÁVÁNÍ" in p for p in context_parts):
            from app.services.search_service import web_search, format_search_context
            log.info("[SEARCH proactive] %r", proactive_query)
            hits = web_search(proactive_query, max_results=4)
            if hits:
                context_parts = [format_search_context(hits, fetch_first=True)] + context_parts

        prompt_text = "\n\n".join(context_parts + ["Zpráva studenta: " + user_input])

        cached = self.get_cached_response(user_id, conversation_id, prompt_text)
        if cached:
            try:
                return json.loads(cached)
            except json.JSONDecodeError:
                pass

        db_history = self._load_history(user_id, conversation_id)
        try:
            response_text = self._generate_with_fallback(
                config, prompt_text, history=db_history, user_id=user_id,
                model_id=model_id, ai_mode=ai_mode,
            )
        except RateLimitedError as exc:
            return _rate_limited_response(exc.tier, exc.model_id)
        result = _parse_ai_response(response_text)

        # ── Two-pass web search ───────────────────────────────────────────────
        if result.get("needs_search") and result.get("search_query"):
            from app.services.search_service import web_search, format_search_context, format_sources_md
            query       = str(result["search_query"])
            # For create_page fetch the first result's full body for richer content
            fetch_first = result.get("intent") == "create_page"
            search_hits = web_search(query, max_results=4)
            if search_hits:
                search_ctx   = format_search_context(search_hits, fetch_first=fetch_first)
                second_prompt = "\n\n".join([
                    search_ctx,
                    "Původní zpráva studenta: " + user_input,
                    "Na základě výsledků vyhledávání výše odpověz kompletně. "
                    "Do pole message (nebo do page_content_html pro stránky) zahrň nalezené informace. "
                    "needs_search musí být false.",
                ])
                try:
                    response_text2 = self._generate_with_fallback(
                        config, second_prompt, history=db_history,
                        user_id=None,  # nezapočítávat do limitu — součást téhož requestu
                        model_id=model_id, ai_mode=ai_mode,
                    )
                    result2 = _parse_ai_response(response_text2)
                    # Append formatted sources to the message
                    sources_md = format_sources_md(search_hits)
                    if sources_md and result2.get("message"):
                        result2["message"] = result2["message"].rstrip() + "\n\n" + sources_md
                    result = result2
                except Exception:
                    log.exception("_call_api: search follow-up failed, using first response")
                    # Append sources to original message as fallback
                    sources_md = format_sources_md(search_hits)
                    if sources_md and result.get("message"):
                        result["message"] = result["message"].rstrip() + "\n\n" + sources_md

        result_json = json.dumps(result, ensure_ascii=False)
        self._save_exchange(user_id, conversation_id, user_input, result_json)
        self._save_to_cache(user_id, conversation_id, prompt_text, result_json)

        return result

    # ── Grade-context flow ────────────────────────────────────────────────────

    def handle_grades_context(
        self,
        user_id: str,
        conversation_id: "str | None",
        grades: list,
        user_input: str,
        model_id: "str | None" = None,
        ai_mode: str = AI_MODE_NORMAL,
    ) -> dict:
        """Route a message through the context-aware grade/page flow.

        Decision tree (evaluated in priority order):
          1. Modification request  → call model immediately, no confirmation
          2. Awaiting confirmation  → confirm → generate page | else → normal chat
          3. Grade-related query   → wants page → generate | else → confirmation prompt
          4. Unrelated             → normal chat with grades silently in context
        """
        history = self._load_history(user_id, conversation_id)

        # ── 1. Modification ──────────────────────────────────────────────────
        if self._is_modification_request(user_input, history):
            recent = self.filter_recent_grades(grades)
            topics = list({
                (g.get("topic") or g.get("Caption") or "").strip()
                for g in recent
                if (g.get("topic") or g.get("Caption") or "").strip()
            })
            context = [
                "Studentova data (nedávná témata): "
                + json.dumps({"topics": topics, "grade_count": len(recent)}, ensure_ascii=False)
            ]
            try:
                return self._call_api(user_id, conversation_id, user_input, context, self._live_page_config, model_id=model_id, ai_mode=ai_mode)
            except Exception:
                log.exception("handle_grades_context: modification call failed")
                return _error_response()

        # ── 2. Awaiting confirmation ─────────────────────────────────────────
        if self._awaiting_grade_confirmation(history):
            if self._wants_page_now(user_input):
                return self._generate_grade_page(user_id, conversation_id, grades, user_input, model_id=model_id, ai_mode=ai_mode)
            return self.generate_chat_response(user_id, conversation_id, user_input, model_id=model_id, ai_mode=ai_mode)

        # ── 3. Grade-related query ───────────────────────────────────────────
        if self._is_grade_related(user_input):
            if self._wants_page_now(user_input):
                return self._generate_grade_page(user_id, conversation_id, grades, user_input, model_id=model_id, ai_mode=ai_mode)
            confirmation = {
                "message": "Vidím tvoje známky. Chceš, abych vytvořil studijní stránku pro tato témata?",
                "intent": "chat",
                "page_title": None,
                "page_content_html": None,
                "action_label": None,
                "is_test": False,
            }
            # Persist the exchange so _awaiting_grade_confirmation detects state next turn
            self._save_exchange(user_id, conversation_id, user_input, json.dumps(confirmation, ensure_ascii=False))
            return confirmation

        # ── 4. Unrelated query ───────────────────────────────────────────────
        return self.generate_chat_response(user_id, conversation_id, user_input, model_id=model_id, ai_mode=ai_mode)

    def _generate_grade_page(
        self,
        user_id: str,
        conversation_id: "str | None",
        grades: list,
        user_input: str,
        model_id: "str | None" = None,
        ai_mode: str = AI_MODE_NORMAL,
    ) -> dict:
        """Filter grades, validate topics, then generate page via API."""
        recent = self.filter_recent_grades(grades)

        if not recent:
            response = {
                "message": "Nemám žádné známky z posledních 30 dní, ze kterých bych mohl vytvořit stránku.",
                "intent": "chat",
                "page_title": None,
                "page_content_html": None,
                "action_label": None,
                "is_test": False,
            }
            self._save_exchange(user_id, conversation_id, user_input, json.dumps(response, ensure_ascii=False))
            return response

        missing_topic = [
            g for g in recent
            if not (g.get("topic") or g.get("Caption") or "").strip()
        ]
        if missing_topic:
            subjects = ", ".join(
                g.get("subject") or (g.get("Subject") or {}).get("Name") or "neznámý předmět"
                for g in missing_topic
            )
            response = {
                "message": (
                    f"Pro tyto předměty chybí téma: {subjects}. "
                    "Můžeš upřesnit, jaké učivo právě probíráte?"
                ),
                "intent": "chat",
                "page_title": None,
                "page_content_html": None,
                "action_label": None,
                "is_test": False,
            }
            self._save_exchange(user_id, conversation_id, user_input, json.dumps(response, ensure_ascii=False))
            return response

        topics = list({
            (g.get("topic") or g.get("Caption") or "").strip()
            for g in recent
            if (g.get("topic") or g.get("Caption") or "").strip()
        })
        context = [
            "Studentova data: "
            + json.dumps({"topics": topics, "recent_grade_count": len(recent)}, ensure_ascii=False)
        ]
        synthesised = f"Vytvoř studijní stránku pro tato témata: {', '.join(sorted(topics))}"

        try:
            return self._call_api(user_id, conversation_id, synthesised, context, self._live_page_config, model_id=model_id, ai_mode=ai_mode)
        except Exception:
            log.exception("_generate_grade_page: API call failed")
            return _error_response()

    # ── Stateful page modification ────────────────────────────────────────────

    def modify_page(self, user_id: str, conversation_id: "str | None", current_html: str, prompt: str, model_id: "str | None" = None) -> str:
        """Apply a modification prompt to page HTML using persistent conversation history.

        Returns updated HTML string. Raises RateLimitedError when the user has
        exhausted their request budget.
        """
        from app.database.db import log_ai_request

        effective_model = model_id or _DEFAULT_MODEL
        model_info = _MODELS.get(effective_model, _MODELS[_DEFAULT_MODEL])

        history = self._load_history(user_id, conversation_id)
        full_prompt = "\n\n".join([
            "Aktuální HTML obsah stránky:\n" + current_html,
            "Požadavek na úpravu: " + prompt,
        ])
        hist_list = [{"role": h["role"], "text": h["text"]} for h in history]

        # ── Freemium OpenRouter model ─────────────────────────────────────────
        if model_info["group"] == "freemium" and model_info["provider"] == "openrouter":
            try:
                new_html = _call_openrouter(_MODIFY_PROMPT, full_prompt, hist_list, model=effective_model)
            except Exception as _or_exc:
                if _or_status(_or_exc) == 402:
                    log.warning("OpenRouter 402 in modify_page model=%s user=%.8s", effective_model, user_id)
                    from app.database.db import get_subscription_tier
                    raise RateLimitedError(get_subscription_tier(user_id)) from _or_exc
                raise
            log_ai_request(user_id, "openrouter")
            self._save_exchange(user_id, conversation_id, prompt, f"[page modified: {prompt[:120]}]")
            return new_html

        # ── gemini-2.5-flash-lite ─────────────────────────────────────────────
        if effective_model == "gemini-2.5-flash-lite":
            from app.database.db import count_ai_requests, get_subscription_tier
            tier = get_subscription_tier(user_id)
            # Must match the "YYYY-MM-DD HH:MM:SS" format SQLite stores via
            # datetime('now'); isoformat()'s 'T' separator compares wrong.
            since_24h = (datetime.datetime.utcnow() - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            if count_ai_requests(user_id, "gemini-2.5-flash-lite", since_24h) >= _FREEMIUM_GEMINI_DAILY:
                raise RateLimitedError(tier, effective_model)
            log_ai_request(user_id, "gemini-2.5-flash-lite")
            gemini_model = effective_model
        else:
            provider, tier = _resolve_provider(user_id)
            if provider is None:
                raise RateLimitedError(tier)
            log_ai_request(user_id, "gemini")
            gemini_model = effective_model

        hist = [
            types.Content(role=h["role"], parts=[types.Part(text=h["text"])])
            for h in history
            if h.get("role") in ("user", "model") and h.get("text")
        ]
        chat = self._client.chats.create(
            model=gemini_model, config=self._modify_config, history=hist,
        )
        new_html = chat.send_message(full_prompt).text

        self._save_exchange(user_id, conversation_id, prompt, f"[page modified: {prompt[:120]}]")
        return new_html

    # ── Core public methods ───────────────────────────────────────────────────

    def get_response(self, user_id: str, conversation_id: "str | None", user_input: str, student_data: dict = None, model_id: "str | None" = None, ai_mode: str = AI_MODE_NORMAL) -> dict:
        """Primary chat endpoint with mandatory user_id for DB history lookup.

        Queries conversation_history before every API call to maintain context
        across page reloads. Flow: Load History → Check Cache → Call API → Save.
        """
        context = []
        if student_data:
            context.append("Studentova data: " + json.dumps(student_data, ensure_ascii=False))
        try:
            return self._call_api(user_id, conversation_id, user_input, context, self._ai_chat_config, model_id=model_id, ai_mode=ai_mode)
        except Exception:
            log.exception("GeminiService.get_response failed")
            return _error_response()

    def generate_chat_response(
        self,
        user_id: str,
        conversation_id: "str | None",
        user_input: str,
        student_data: dict = None,
        model_id: "str | None" = None,
        ai_mode: str = AI_MODE_NORMAL,
    ) -> dict:
        """Alias for get_response — kept for call-site compatibility."""
        return self.get_response(user_id, conversation_id, user_input, student_data, model_id=model_id, ai_mode=ai_mode)

    def explain_term(self, user_id: str, conversation_id: "str | None", term: str, model_id: "str | None" = None, ai_mode: str = AI_MODE_NORMAL) -> dict:
        """Return a concise explanation of a user-selected text snippet."""
        config = types.GenerateContentConfig(
            system_instruction=_EXPLAIN_PROMPT,
            response_mime_type="application/json",
        )
        try:
            return self._call_api(user_id, conversation_id, term, [], config, model_id=model_id, ai_mode=ai_mode)
        except Exception:
            log.exception("GeminiService.explain_term failed")
            return _error_response()

    def _generate_summary_period(
        self,
        grades: list,
        all_subjects: list,
        system_prompt: str,
    ) -> dict:
        """Shared Gemini call for both weekly and daily summaries. Stateless."""
        payload = json.dumps(
            {"grades": grades, "all_subjects_averages": all_subjects},
            ensure_ascii=False,
        )
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
        )
        try:
            return _parse_ai_response(self._generate_with_fallback(config, payload))
        except Exception:
            log.exception("GeminiService._generate_summary_period failed")
            return {
                "summary": "Shrnutí se nepodařilo vygenerovat.",
                "weak_subjects": [],
                "study_plan": "",
                "poor_performance": False,
                "cta": None,
                "error": "AI unavailable",
            }

    def generate_weekly_summary(
        self,
        user_id: str,
        weekly_grades: list,
        all_subjects: list,
    ) -> dict:
        return self._generate_summary_period(weekly_grades, all_subjects, _WEEKLY_SUMMARY_PROMPT)

    def generate_daily_summary(
        self,
        user_id: str,
        daily_grades: list,
        all_subjects: list,
    ) -> dict:
        return self._generate_summary_period(daily_grades, all_subjects, _DAILY_SUMMARY_PROMPT)

    def generate_study_plan(self, user_id: str, context: dict) -> dict:
        """Generate a personalised study plan from timetable, homeworks and weak subjects."""
        config = types.GenerateContentConfig(
            system_instruction=_STUDY_PLAN_PROMPT,
            response_mime_type="application/json",
        )
        try:
            return _parse_ai_response(self._generate_with_fallback(
                config, json.dumps(context, ensure_ascii=False)
            ))
        except Exception:
            log.exception("GeminiService.generate_study_plan failed")
            return {
                "plan": "Studijní plán se nepodařilo vygenerovat.",
                "priority_tasks": [],
                "study_slots": "",
                "tip": None,
                "error": "AI unavailable",
            }

    # ── /skill command ────────────────────────────────────────────────────────

    def handle_skill_command(self, user_id: str, message: str) -> dict:
        """Route /skill commands and in-progress skill-creation flows."""
        parts = message.strip().split(None, 2)
        sub   = parts[1].lower() if len(parts) > 1 else ""

        if sub == "cancel":
            _clear_pending_skill(user_id)
            return _chat_reply("Tvorba skilu byla zrušena.")

        pending = _get_pending_skill(user_id)
        if pending is not None:
            return self._skill_create_step(user_id, message, pending)

        if sub == "create":
            from app.database.db import get_subscription_tier
            if get_subscription_tier(user_id) != "premium" and len(_list_skills(user_id)) >= _FREE_MAX_SKILLS:
                return _chat_reply(
                    f"Ve free verzi můžeš mít {_FREE_MAX_SKILLS} vlastní skill. "
                    "Smaž stávající (`/skill delete [jméno]`), nebo přejdi na Premium pro neomezené skilly. ✦"
                )
            _set_pending_skill(user_id, 0, {})
            return _chat_reply(
                "Vytváříme nový skill. Popiš jeho účel a chování — "
                "jak má reagovat na uživatele a co je jeho specialita?"
            )

        if sub == "list":
            skills = _list_skills(user_id)
            if not skills:
                return _chat_reply("Žádné uložené skilly. Vytvoř první pomocí `/skill create`.")
            names = "  \n".join(f"• `{s['name']}`" for s in skills)
            return _chat_reply(f"Tvoje skilly:\n{names}")

        if sub == "delete" and len(parts) > 2:
            name = parts[2].strip()
            if _delete_skill(user_id, name):
                return _chat_reply(f"Skill `{name}` byl smazán.")
            return _chat_reply(f"Skill `{name}` neexistuje.")

        if sub:
            context = parts[2] if len(parts) > 2 else ""
            return self._skill_use(user_id, sub, context)

        return _chat_reply(
            "**Skill příkazy:**  \n"
            "• `/skill create` — vytvoří nový skill  \n"
            "• `/skill [jméno] [zpráva]` — použije skill  \n"
            "• `/skill list` — vypíše skilly  \n"
            "• `/skill delete [jméno]` — smaže skill  \n"
            "• `/skill cancel` — zruší tvorbu skilu"
        )

    def _skill_create_step(self, user_id: str, user_input: str, pending: dict) -> dict:
        lower = user_input.strip().lower()
        if lower in ("/skill cancel", "cancel", "zrušit", "konec", "stop"):
            _clear_pending_skill(user_id)
            return _chat_reply("Tvorba skilu byla zrušena. Začni znovu pomocí `/skill create`.")

        step = pending["step"]
        data = pending["data"]

        if step == 0:
            # User gave description → refine with Gemini, ask for name
            try:
                cfg     = types.GenerateContentConfig(system_instruction=_SKILL_REFINE_PROMPT)
                refined = self._generate_with_fallback(cfg, user_input).strip()
            except Exception:
                log.exception("_skill_create_step: refinement failed")
                refined = user_input.strip()

            _set_pending_skill(user_id, 1, {"description": refined})
            return _chat_reply(
                f"Navrhovaný systémový prompt:\n\n> {refined}\n\n"
                "Jak chceš skill pojmenovat? (použij pomlčkový slug, např. `strict-teacher`)  \n"
                "Nebo napiš `upravit` pro úpravu popisu."
            )

        if step == 1:
            if lower in ("upravit", "uprav", "edit", "změnit", "změn"):
                _set_pending_skill(user_id, 0, {})
                return _chat_reply("Dobře, zkusíme znovu. Jak má skill vypadat?")

            name = re.sub(r"[^a-z0-9\-]", "", user_input.strip().lower().replace(" ", "-"))
            if not name:
                return _chat_reply("Jméno musí obsahovat písmena nebo číslice. Zkus znovu.")

            try:
                _save_skill(user_id, name, data.get("description", ""))
            except Exception:
                _clear_pending_skill(user_id)
                return _chat_reply("Nepodařilo se uložit skill. Zkus to znovu.")

            _clear_pending_skill(user_id)
            return _chat_reply(
                f"✓ Skill `{name}` uložen.\n\nPoužití: `/skill {name} [tvoje zpráva]`"
            )

        _clear_pending_skill(user_id)
        return _chat_reply("Stav tvorby skilu byl resetován. Začni znovu: `/skill create`")

    def _skill_use(self, user_id: str, name: str, context: str) -> dict:
        description = _get_skill(user_id, name)
        if description is None:
            return _chat_reply(
                f"Skill `{name}` neexistuje. Vypiš dostupné pomocí `/skill list`."
            )
        if not context.strip():
            return _chat_reply(f"Skill `{name}` je připraven. Co chceš vědět nebo udělat?")

        cfg = types.GenerateContentConfig(system_instruction=description)
        try:
            reply = self._generate_with_fallback(cfg, context).strip()
        except Exception:
            log.exception("_skill_use: API call failed for skill=%s", name)
            reply = "Omlouvám se, nastala chyba při volání AI."

        return _chat_reply(reply)

    def get_proactive_insights(self, data: dict) -> dict:
        try:
            return _parse_ai_response(self._generate_with_fallback(
                self._insights_config, json.dumps(data, ensure_ascii=False)
            ))
        except Exception:
            log.exception("GeminiService.get_proactive_insights failed")
            return {
                "alert": "", "recommendation": "", "exercise": "",
                "chat_prompt": "", "error": "AI insights unavailable",
            }

    def send_chat_message(self, history: list, message: str) -> str:
        """Legacy chat method — uses caller-supplied history (no DB persistence)."""
        try:
            hist = [
                types.Content(role=item["role"], parts=[types.Part(text=item["text"])])
                for item in history
                if item.get("role") in ("user", "model") and item.get("text")
            ]
            chat = self._client.chats.create(
                model=self._model, config=self._chat_config, history=hist,
            )
            return chat.send_message(message).text
        except Exception:
            log.exception("GeminiService.send_chat_message failed")
            return "Omlouvám se, nastala chyba. Zkus to prosím znovu."

    def regenerate_page(
        self,
        current_html: str,
        prompt: str,
        student_data: dict = None,
        user_id: "str | None" = None,
    ) -> str:
        """Stateless page regen — no history. Raises RateLimitedError when budget is exhausted."""
        from app.database.db import log_ai_request

        parts = []
        if student_data:
            parts.append("Studentova data: " + json.dumps(student_data, ensure_ascii=False))
        parts.append("Aktuální HTML obsah:\n" + current_html)
        parts.append("Požadavek studenta: " + prompt)
        contents = "\n\n".join(parts)
        config = types.GenerateContentConfig(system_instruction=_REGEN_PROMPT)

        if user_id:
            provider, tier = _resolve_provider(user_id)
            if provider is None:
                raise RateLimitedError(tier)
            log_ai_request(user_id, "gemini")

        response = self._client.models.generate_content(
            model=self._model, config=config, contents=contents,
        )
        return response.text



def _parse_ai_response(text: str) -> dict:
    """Parse an AI response string into the expected dict.

    Handles three common failure modes from OpenRouter free models:
    1. Response wrapped in markdown code fences (```json ... ```)
    2. Literal control characters inside JSON string values (invalid JSON)
    3. Plain-text response instead of JSON
    """
    s = text.strip()

    # Strip markdown code fences some models add around JSON
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s.strip())

    # First attempt: parse as-is
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # Second attempt: replace literal control characters inside strings
    # (newlines, tabs etc. that should be \n / \t but aren't escaped)
    cleaned = re.sub(r'(?<!\\)([\x00-\x09\x0b\x0c\x0e-\x1f\x7f])', " ", s)
    # Also handle bare \r\n / \n inside quoted strings
    cleaned = re.sub(r'(?<=[^\\])\n', r"\\n", cleaned)
    cleaned = re.sub(r'(?<=[^\\])\r', "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Third attempt: the JSON broke mid-stream (degenerate model output,
    # truncation). Salvage at least the "message" string so the user gets the
    # text instead of the raw {"message": …} wrapper.
    m = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)', s)
    if m and m.group(1).strip():
        try:
            salvaged = json.loads('"' + m.group(1) + '"')
        except json.JSONDecodeError:
            salvaged = m.group(1)
        log.warning("_parse_ai_response: salvaged message from broken JSON, "
                    "first 120 chars: %s", s[:120])
        return _chat_reply(_strip_degenerate_tail(salvaged))

    # Fallback: treat the whole response as a plain-text chat message
    log.warning("_parse_ai_response: falling back to plain-text wrap, first 120 chars: %s", s[:120])
    return _chat_reply(_strip_degenerate_tail(text))


def _chat_reply(message: str) -> dict:
    return {
        "message": message,
        "intent": "chat",
        "page_title": None,
        "page_content_html": None,
        "action_label": None,
        "is_test": False,
    }
