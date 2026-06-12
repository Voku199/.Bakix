"""OpenRouter fallback client (used when Gemini is over quota) and
helpers for bridging Gemini GenerateContentConfig to plain chat messages."""

import logging
import os
import re

import requests
from google.genai import types

log = logging.getLogger(__name__)

def _si_to_str(config: "types.GenerateContentConfig") -> str:
    """Extract the system instruction string from a GenerateContentConfig."""
    si = config.system_instruction
    if isinstance(si, str):
        return si
    if si is not None:
        try:
            return "".join(getattr(p, "text", "") for p in getattr(si, "parts", [si]))
        except Exception:
            pass
    return ""


def _or_status(exc: Exception) -> "int | None":
    """Return the HTTP status code from a requests.HTTPError, or None."""
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", None) if resp is not None else None


# ── OpenRouter fallback ───────────────────────────────────────────────────────

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _is_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(t in msg for t in ("429", "quota", "rate_limit", "resource_exhausted", "too many request"))


def _strip_degenerate_tail(text: str) -> str:
    """Free models sometimes lock into a repetition loop and pad the end of
    the response with the same short token over and over ("</</</…",
    "ano ano ano…"). A short chunk repeated 6+ times at the very end is never
    legitimate output — cut the loop, then drop a dangling "<"/"</" fragment
    the loop may have left behind."""
    s = re.sub(r"([^\n]{1,16}?)(?:\1){5,}\s*$", r"\1", text.strip())
    return re.sub(r"\s*</?\s*$", "", s)


def _call_openrouter(
    system_instruction: str,
    contents: str,
    history: "list[dict] | None" = None,
    json_mode: bool = False,
    model: "str | None" = None,
    max_tokens: "int | None" = None,
) -> str:
    """Call OpenRouter as a drop-in fallback for Gemini. Returns raw response text."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set — cannot use OpenRouter fallback")
    if model is None:
        model = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
    messages: list[dict] = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    for h in (history or []):
        if h.get("text"):
            messages.append({
                "role":    "assistant" if h.get("role") == "model" else "user",
                "content": h["text"],
            })
    messages.append({"role": "user", "content": contents})
    body: dict = {
        "model": model,
        "messages": messages,
        # Free models are prone to repetition loops ("</</</…") — conservative
        # sampling plus a repetition penalty keeps them on the rails.
        "temperature": 0.6,
        "top_p": 0.9,
        "repetition_penalty": 1.1,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if max_tokens:
        body["max_tokens"] = max_tokens

    prompt_chars = sum(len(m["content"]) for m in messages)
    history_turns = len(history) if history else 0
    log.info("[OR] → model=%s json_mode=%s turns=%s prompt_chars=%s max_tokens=%s", model, json_mode, history_turns, prompt_chars, max_tokens or '∞')

    import time
    t0 = time.perf_counter()
    resp = requests.post(
        _OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    elapsed = time.perf_counter() - t0

    resp.raise_for_status()
    raw = resp.json()
    content = _strip_degenerate_tail(raw["choices"][0]["message"]["content"] or "")
    tokens_in  = (raw.get("usage") or {}).get("prompt_tokens", "?")
    tokens_out = (raw.get("usage") or {}).get("completion_tokens", "?")
    log.info("[OR] ← %.2fs tokens=%s→%s response_chars=%s", elapsed, tokens_in, tokens_out, len(content))
    return content
