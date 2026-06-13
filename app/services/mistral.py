"""Mistral client — extra selectable chat models via the Mistral SDK.

Mirrors openrouter.py on purpose: `_call_mistral` has the exact same signature
as `_call_openrouter`, so GeminiService._generate_with_fallback can route
freemium "mistral" models through it with the same code path it uses for
OpenRouter. The models themselves are registered in ai_models.py with
provider="mistral".

Requires the MISTRAL_API_KEY env var and the `mistralai` SDK
(`pip install mistralai`). Both are checked lazily so the rest of the app keeps
working when Mistral isn't configured — selecting a Mistral model without them
simply errors for that one request.
"""

import logging
import os
import time

log = logging.getLogger(__name__)

# Used when a Mistral request arrives without an explicit model id.
_DEFAULT_MISTRAL_MODEL = "ministral-8b-2512"


def _call_mistral(
    system_instruction: str,
    contents: str,
    history: "list[dict] | None" = None,
    json_mode: bool = False,
    model: "str | None" = None,
    max_tokens: "int | None" = None,
) -> str:
    """Call Mistral as a drop-in alternative backend. Returns raw response text.

    Same signature/shape as openrouter._call_openrouter so it slots into the
    same routing branch. `history` items are {"role": "user"|"model", "text": str}
    (DB shape); the "model" role is mapped to OpenAI-style "assistant".
    """
    api_key = os.environ.get("MISTRAL_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY not set — cannot use Mistral models")

    try:
        from mistralai import Mistral
    except ImportError as exc:
        raise RuntimeError(
            "'mistralai' SDK not installed — run: pip install mistralai"
        ) from exc

    model = model or _DEFAULT_MISTRAL_MODEL

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

    kwargs: dict = {"model": model, "messages": messages}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens

    prompt_chars  = sum(len(m["content"]) for m in messages)
    history_turns = len(history) if history else 0
    log.info("[MISTRAL] → model=%s json_mode=%s turns=%s prompt_chars=%s max_tokens=%s",
             model, json_mode, history_turns, prompt_chars, max_tokens or '∞')

    t0     = time.perf_counter()
    client = Mistral(api_key=api_key)
    resp   = client.chat.complete(**kwargs)
    elapsed = time.perf_counter() - t0

    content = resp.choices[0].message.content or ""
    # Some SDK versions return the content as a list of chunks rather than a str.
    if isinstance(content, list):
        content = "".join(
            c if isinstance(c, str) else getattr(c, "text", "")
            for c in content
        )

    usage      = getattr(resp, "usage", None)
    tokens_in  = getattr(usage, "prompt_tokens", "?") if usage else "?"
    tokens_out = getattr(usage, "completion_tokens", "?") if usage else "?"
    log.info("[MISTRAL] ← %.2fs tokens=%s→%s response_chars=%s",
             elapsed, tokens_in, tokens_out, len(content))
    return content
