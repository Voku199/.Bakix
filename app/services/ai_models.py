"""Model registry, rate-limit tiers and per-tier model resolution."""

import datetime
import logging

log = logging.getLogger(__name__)

# ── Rate-limit tiers ──────────────────────────────────────────────────────────
_FREE_GEMINI_DAILY    = 5    # Gemini requests per 24 h (free)
_PREMIUM_GEMINI_DAILY = 50   # Gemini requests per 24 h (premium)
_OR_REQUESTS_PER_6H   = 50   # OpenRouter requests per 6 h (both tiers)
_FREEMIUM_GEMINI_DAILY = 10  # Daily limit for gemini-2.5-flash-lite
_FREEMIUM_OR_HISTORY    = 6    # Max history turns — Normal mode
_FREEMIUM_OR_MAX_TOKENS = 800  # Max response tokens — Normal mode

# ── AI response modes ─────────────────────────────────────────────────────────
AI_MODE_NORMAL   = "normal"    # Fast: trimmed history + capped tokens
AI_MODE_THINKING = "thinking"  # Full: 20 turns + unlimited tokens

_DEFAULT_MODEL = "gemini-3.1-flash-lite"
# Model handed to free users (and used when a free user requests a Pro model).
# Belongs to the "freemium" group, so it stays within the free allowance.
_FREE_DEFAULT_MODEL = "gemini-2.5-flash-lite"

# Free-tier caps (Premium = unlimited). Pages/chats are enforced in the routes.
_FREE_MAX_SKILLS = 1

# ── Model registry ────────────────────────────────────────────────────────────
_MODELS: "dict[str, dict]" = {
    # Pro tier — count against normal Gemini/OpenRouter rate limits
    "gemini-3.1-flash-lite":  {"display_name": "Gemini 3.1 Flash Lite",  "provider": "gemini",      "group": "pro"},
    "gemini-3.5-flash":       {"display_name": "Gemini 3.5 Flash",       "provider": "gemini",      "group": "pro"},
    "gemini-3-flash-preview": {"display_name": "Gemini 3 Flash Preview", "provider": "gemini",      "group": "pro"},
    # Freemium tier — gemini-2.5-flash-lite has its own 10/day limit
    "gemini-2.5-flash-lite":  {"display_name": "Gemini 2.5 Flash Lite",  "provider": "gemini",      "group": "freemium"},
    # Freemium OpenRouter free models — bypass Gemini budget, go direct
    "nvidia/nemotron-3-super-120b-a12b:free": {"display_name": "Nemotron 120B",  "provider": "openrouter", "group": "freemium"},
    "poolside/laguna-m.1:free":               {"display_name": "Laguna M.1",     "provider": "openrouter", "group": "freemium"},
    "openrouter/free":                        {"display_name": "OpenRouter Auto", "provider": "openrouter", "group": "freemium"},
    "openai/gpt-oss-120b:free":               {"display_name": "GPT OSS 120B",   "provider": "openrouter", "group": "freemium"},
    "qwen/qwen3-next-80b-a3b-instruct:free":  {"display_name": "Qwen3 80B",      "provider": "openrouter", "group": "freemium"},
    "google/gemma-4-26b-a4b-it:free":         {"display_name": "Gemma 4 26B",    "provider": "openrouter", "group": "freemium"},
}


class RateLimitedError(Exception):
    """Raised when a user has exhausted all available AI request budget."""
    def __init__(self, tier: str, model_id: "str | None" = None) -> None:
        self.tier = tier
        self.model_id = model_id
        super().__init__(f"rate_limited:{tier}:{model_id or ''}")


def _resolve_provider(user_id: str) -> "tuple[str | None, str]":
    """Return (provider, tier) for user_id.

    provider is 'gemini' or None (daily quota exhausted).
    OpenRouter is NOT used here — it only serves as a server-side fallback
    when Gemini throws a quota error (handled in _generate_with_fallback).
    """
    from app.database.db import (
        get_subscription_tier,
        count_ai_requests,
    )
    tier = get_subscription_tier(user_id)
    now = datetime.datetime.utcnow()
    # SQLite stores datetime('now') as "YYYY-MM-DD HH:MM:SS" (space, no microseconds)
    # — isoformat() uses 'T' separator which sorts differently, so we must match the format.
    since_24h = (now - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")

    gemini_limit = _PREMIUM_GEMINI_DAILY if tier == "premium" else _FREE_GEMINI_DAILY
    used = count_ai_requests(user_id, "gemini", since_24h)

    log.info("[RATE LIMIT] user=%s tier=%s model=gemini used=%s/%s", user_id[:8], tier, used, gemini_limit)

    if used >= gemini_limit:
        log.info("[RATE LIMIT] limit reached: user=%s (%s/%s)", user_id[:8], used, gemini_limit)
        return (None, tier)

    return ("gemini", tier)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _error_response() -> dict:
    return {
        "message": "Omlouvám se, nastala chyba. Zkus to prosím znovu.",
        "intent": "chat",
        "page_title": None,
        "page_content_html": None,
        "action_label": None,
        "is_test": False,
    }


def _rate_limited_response(tier: str, model_id: "str | None" = None) -> dict:
    if model_id == "gemini-2.5-flash-lite":
        msg = (
            "Denní limit Gemini 2.5 Flash Lite (10 požadavků) byl vyčerpán. "
            "Zvol jiný model nebo zkus zítra."
        )
    elif model_id and _MODELS.get(model_id, {}).get("group") == "freemium":
        msg = "Freemium model momentálně není dostupný. Zkus jiný model."
    elif tier == "premium":
        msg = (
            "Dosáhl(a) jsi denního limitu Premium plánu (50 dotazů). "
            "Limit se obnoví za 24 hodin."
        )
    else:
        msg = (
            "Dosáhl(a) jsi limitu bezplatné verze (5 AI dotazů za den). "
            "Upgraduj na **Premium** pro 50 dotazů denně!"
        )
    return {
        "message":      msg,
        "intent":       "chat",
        "page_title":   None,
        "page_content_html": None,
        "action_label": None,
        "is_test":      False,
        "rate_limited": True,
        "tier":         tier,
    }


def list_models() -> list:
    """Return all available models for the frontend."""
    return [{"id": k, **v} for k, v in _MODELS.items()]


def is_valid_model(model_id: str) -> bool:
    return model_id in _MODELS


def is_premium_model(model_id: "str | None") -> bool:
    """True for models reserved for Premium (the 'pro' group)."""
    info = _MODELS.get(model_id or "")
    return bool(info and info["group"] == "pro")


def resolve_model_for_tier(model_id: "str | None", tier: str) -> "tuple[str | None, bool]":
    """Return (effective_model_id, was_downgraded) for the given tier.

    Premium keeps its choice (None → _DEFAULT_MODEL). Free users may only use
    'freemium' models; a Pro choice (or the Pro default) is swapped for
    _FREE_DEFAULT_MODEL and flagged so the UI can nudge an upgrade.
    """
    if tier == "premium":
        return model_id, False
    if model_id and _MODELS.get(model_id, {}).get("group") == "freemium":
        return model_id, False
    return _FREE_DEFAULT_MODEL, True

