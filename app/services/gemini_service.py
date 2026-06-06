import datetime
import hashlib
import json
import logging
import os
import re

import requests
from google import genai
from google.genai import types

from app.database.connection import get_connection

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

    print(f"[RATE LIMIT] user={user_id[:8]} tier={tier} model=gemini used={used}/{gemini_limit}")

    if used >= gemini_limit:
        print(f"[RATE LIMIT] Dosaženo limitu! user={user_id[:8]} ({used}/{gemini_limit})")
        return (None, tier)

    return ("gemini", tier)


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


# ── System prompts ────────────────────────────────────────────────────────────

_INSIGHTS_PROMPT = (
    "Jsi osobní AI asistent pro studenty. Analyzuj data z Bakalářů. Vše piš česky. "
    "Pokud studenta upozorňuješ na horší známky, buď konstruktivní a navrhni konkrétní kroky. "
    "Vždy přidej krátké cvičení a otázku pro pokračování v chatu. "
    "Odpověz POUZE validním JSON objektem: "
    '{"alert": "string", "recommendation": "string", "exercise": "string", "chat_prompt": "string"}'
)

_CHAT_PROMPT = (
    "Jsi osobní AI asistent pro studenty. Odpovídej vždy česky. "
    "Buď konstruktivní, přívětivý a konkrétní. Pomáhej studentovi pochopit látku a motivuj ho."
)

_REGEN_PROMPT = (
    "Jsi AI generátor vzdělávacích HTML stránek. "
    "Uprav poskytnutý HTML obsah stránky podle požadavku studenta. "
    "Vrať POUZE čisté HTML tělo (bez <html>/<head>/<body> tagů, bez markdown bloků). "
    "KRITICKÉ: NIKDY neměň předmět ani jazyk stránky — uprav POUZE formu, obtížnost nebo přidej "
    "obsah ke stejnému tématu. Stránka o češtině zůstane o češtině, o matematice o matematice. "
    "Zachovej gp-* CSS třídy (gp-article, gp-hero, gp-section, gp-label, gp-quiz, gp-q, "
    "gp-opt, gp-btn, gp-result, gp-cards, gp-card atd.) a celkovou strukturu původního obsahu. "
    "U kvízových otázek .gp-q zachovej atribut data-answer; NEpiš <script> ani onclick. "
    "Matematiku piš/zachovej v LaTeXu ($...$ inline, $$...$$ blokově)."
)

_MODIFY_PROMPT = (
    "Jsi AI editor vzdělávacích HTML stránek s přístupem k historii konverzace. "
    "Uprav HTML stránku přesně podle aktuálního požadavku. "
    "Zohledni předchozí instrukce z kontextu (např. moderní design, zaměření na konkrétní předmět). "
    "Vrať POUZE čisté HTML tělo (bez <html>/<head>/<body> tagů, bez markdown bloků, bez komentářů). "
    "KRITICKÉ: NIKDY neměň předmět ani jazyk stránky — uprav POUZE formu, obtížnost nebo přidej "
    "obsah ke stejnému tématu. Pokud je stránka o češtině, zůstane o češtině. "
    "Zachovej gp-* CSS třídy (gp-article, gp-hero, gp-section, gp-label, gp-quiz, gp-q, "
    "gp-opt, gp-btn, gp-result, gp-cards, gp-card atd.) a celkovou strukturu, "
    "pokud ji požadavek explicitně nemění. "
    "U kvízových otázek .gp-q zachovej atribut data-answer; NEpiš <script> ani onclick. "
    "Matematiku piš/zachovej v LaTeXu ($...$ inline, $$...$$ blokově)."
)

_AI_CHAT_PROMPT = (
    "Jsi proaktivní AI vzdělávací asistent pro studenty středních a základních škol. "
    "Vždy odpovídej česky. Buď podporující, konkrétní a akční.\n\n"

    "ROZPOZNÁNÍ ZÁMĚRU — určuj VŽDY v tomto pořadí priority:\n"
    "A) MODIFICATION (→ intent=create_page): uživatel žádá ÚPRAVU existujícího materiálu. "
    "Signály: 'přidej', 'uprav', 'změň', 'modernější', 'více otázek', 'méně textu', "
    "'zaměř se na', 'focus on', 'rewrite', 'make it', 'vylepši', 'přepiš'. "
    "→ Okamžitě proveď požadovanou transformaci, NIKDY nezačínaj otázkou o vytvoření stránky.\n"
    "B) CREATE (→ intent=create_page): student explicitně žádá NOVÝ studijní materiál nebo stránku.\n"
    "C) GRADE_ANALYSIS: student zpráva obsahuje RAW data o známkách — konkrétní čísla s předměty "
    "(vzor: 'Fyzika: 3', 'dostal jsem 2 z matiky', seznam hodnocení). "
    "POUZE tehdy nabídni vytvoření stránky. Pokud jsou ke známkám přiložena témata, "
    "použij je jako primární kontext. Pokud téma chybí NEBO je známka starší než 30 dní, "
    "zeptej se: 'Z jakého učiva tato hodnocení pochází?'\n"
    "D) CHAT: vše ostatní — obecný dotaz, pomoc, vysvětlení.\n\n"

    "KRITICKÁ PRAVIDLA:\n"
    "1. Záměr A (modification) přebíjí vše — i pokud jsou dostupná data o známkách.\n"
    "2. Data o známkách v kontextu NEZPŮSOBÍ nabídku vytvoření stránky, pokud se na ně student "
    "sám EXPLICITNĚ neptá (vzorem popsaným v C).\n"
    "3. Nikdy neopakuj 'Vidím tvoje známky...' — tuto větu použij NEJVÝŠE JEDNOU za konverzaci.\n\n"

    "PRAVIDLA GENEROVÁNÍ:\n"
    "1. Vytvoř KOMPLETNÍ materiál: (a) výklad s příklady, "
    "(b) kvíz NEBO kartičky (viz pravidlo 6), (c) sekci .gp-improve s návrhy.\n"
    "2. HTML self-contained — žádné externí CSS/JS.\n"
    "3. Kvíz: každá otázka .gp-q má atribut data-answer se správnou value (viz HTML STRUKTURA). "
    "NEpiš žádný <script> ani onclick — kontrolu odpovědí zajistí šablona.\n"
    "4. Nastav is_test=true pokud zpráva obsahuje 'test', 'prověrka' nebo 'kvíz'.\n"
    "5. TÉMA LOCK: NIKDY neměň předmět ani jazyk stránky při úpravě — "
    "uprav POUZE formu, obtížnost nebo přidej obsah ke STEJNÉMU tématu.\n"
    "6. KARTIČKY: pokud žádá 'kartičky', 'flashcards' nebo 'karty' → "
    "vytvoř sekci .gp-cards (přední strana = pojem, zadní = překlad/definice). "
    "Nepřidávej kvíz, jen kartičky. Struktura viz HTML STRUKTURA níže.\n"
    "7. SVG: kde pomůže diagram nebo schéma, přidej inline "
    "<svg class='gp-svg' viewBox='0 0 W H' xmlns='http://www.w3.org/2000/svg'>. "
    "Jednoduché tvary (rect, circle, line, text, path), max 400×200.\n"
    "8. MATEMATIKA: vzorce, rovnice a výpočty piš v LaTeXu — vykreslí se přes KaTeX. "
    "Inline mezi $...$, blokově (na samostatném řádku) mezi $$...$$. "
    "Příklady: $c^2 = 117$, $$\\sqrt{117} \\approx 10{,}82\\text{ dm}$$. "
    "Desetinnou čárku piš jako {,} (např. 10{,}82). Funguje to jak v 'message', "
    "tak v 'page_content_html'.\n\n"

    "FORMÁT ODPOVĚDI — vrať POUZE validní JSON:\n"
    '  "message"           – odpověď česky (1-3 věty),\n'
    '  "intent"            – "chat" nebo "create_page",\n'
    '  "page_title"        – název stránky (jen pro create_page, jinak null),\n'
    '  "page_content_html" – HTML tělo bez wrapper tagů (jen pro create_page, jinak null),\n'
    '  "action_label"      – text tlačítka (jen pro create_page, jinak null),\n'
    '  "is_test"           – true/false,\n'
    '  "needs_search"      – true pokud potřebuješ aktuální/faktické info (text písně, data, definice, atd.),\n'
    '  "search_query"      – přesný vyhledávací dotaz (string, nebo null).\n\n'

    "KRITICKÉ — POVINNÉ VYHLEDÁVÁNÍ:\n"
    "Pro JAKÝKOLI konkrétní text (píseň, báseň, citát, recept, článek, historická data) "
    "MUSÍŠ nejprve vyhledat. NIKDY nevymýšlej text písně nebo básně ze své hlavy — "
    "vždy nastav needs_search=true a search_query=přesný dotaz (jméno umělce + název díla + 'text'). "
    "Například: user chce stránku o písni → needs_search=true, search_query='Karel Kryl Anděl text písně'. "
    "Teprve po obdržení výsledků vytvoř stránku s reálným obsahem.\n\n"

    "HTML STRUKTURA — používej přesně tyto CSS třídy (jsou stylizovány šablonou):\n"
    "<article class='gp-article'>\n"
    "  <div class='gp-hero'>\n"
    "    <span class='gp-badge'>Studijní materiál</span>\n"
    "    <h1 class='gp-title'>NADPIS TÉMATU</h1>\n"
    "    <p class='gp-lead'>Stručný popis obsahu — 1 věta.</p>\n"
    "  </div>\n"
    "  <section class='gp-section'>\n"
    "    <h2 class='gp-label'>📖 Výklad</h2>\n"
    "    <!-- odstavce výkladu; klíčové pojmy ve <strong>; seznamy ve <ul class='gp-list'><li>...</li></ul>;\n"
    "         definice ve <dl class='gp-defs'><dt>Pojem</dt><dd>Vysvětlení</dd></dl> -->\n"
    "  </section>\n"
    "  <section class='gp-section'>\n"
    "    <h2 class='gp-label'>✏️ Otestuj se</h2>\n"
    "    <div class='gp-quiz'>\n"
    "      <div class='gp-q' id='q1' data-answer='b'>\n"
    "        <p class='gp-qt'><strong>1.</strong> Znění otázky?</p>\n"
    "        <label class='gp-opt'><input type='radio' name='q1' value='a'> Možnost A</label>\n"
    "        <label class='gp-opt'><input type='radio' name='q1' value='b'> Správná možnost B</label>\n"
    "        <label class='gp-opt'><input type='radio' name='q1' value='c'> Možnost C</label>\n"
    "      </div>\n"
    "      <!-- minimálně 3 otázky; každá .gp-q má unikátní id (q1, q2, q3...) "
    "a data-answer = value správné odpovědi -->\n"
    "    </div>\n"
    "    <button class='gp-btn' data-gp-check>Zkontrolovat odpovědi ✓</button>\n"
    "    <div class='gp-result' id='gp-res'></div>\n"
    "  </section>\n"
    "  <!-- ALTERNATIVA ke kvízu — použij pokud žádá kartičky (pravidlo 6): -->\n"
    "  <!-- <section class='gp-section'>\n"
    "    <h2 class='gp-label'>🃏 Kartičky</h2>\n"
    "    <p class='gp-card-hint'>Klikni na kartičku pro otočení</p>\n"
    "    <div class='gp-cards'>\n"
    "      <div class='gp-card'><div class='gp-card-inner'>\n"
    "        <div class='gp-card-front'>Přední strana (pojem)</div>\n"
    "        <div class='gp-card-back'>Zadní strana (překlad / definice)</div>\n"
    "      </div></div>\n"
    "      <!-- opakuj pro každou kartičku -->\n"
    "    </div>\n"
    "  </section> -->\n"
    "  <!-- SVG diagram (přidej volně kde pomůže — mimo sekce i uvnitř): -->\n"
    "  <!-- <svg class='gp-svg' viewBox='0 0 300 120' xmlns='http://www.w3.org/2000/svg'>\n"
    "    <rect x='10' y='10' width='80' height='40' rx='6' fill='#b5451b'/>\n"
    "    <text x='50' y='35' text-anchor='middle' fill='#fff' font-size='12'>Pojem</text>\n"
    "  </svg> -->\n"
    "  <div class='gp-improve'>\n"
    "    <ul>\n"
    "      <li>Konkrétní návrh na rozšíření 1</li>\n"
    "      <li>Konkrétní návrh na rozšíření 2</li>\n"
    "    </ul>\n"
    "  </div>\n"
    "</article>"
)

_LIVE_PAGE_CONSTRAINTS = (
    "\n\nKONTEXT ŽIVÉ STRÁNKY:\n"
    "- Použij POUZE témata z nedávných známek (posledních 30 dní).\n"
    "- Pokud je téma přiloženo ke známce, použij ho jako primární kontext.\n"
    "- Pokud téma chybí nebo je známka starší 30 dní: zeptej se na aktuální učivo.\n"
    "- Při úpravě stránky (modification): okamžitě proveď, nikdy se znovu neptej.\n"
)

_LIVE_PAGE_PROMPT = _AI_CHAT_PROMPT + _LIVE_PAGE_CONSTRAINTS

_EXPLAIN_PROMPT = (
    "Jsi AI tutor pro středoškolské studenty. "
    "Student označil konkrétní text a chce ho vysvětlit. "
    "Vždy odpovídej česky. Buď stručný (2–4 věty), jasný a srozumitelný pro studenta střední školy. "
    "Nevracej otázky zpět. Nenavrhuj tvorbu studijní stránky. "
    "Odpověz POUZE validním JSON objektem: "
    '{"message": "string", "intent": "chat", '
    '"page_title": null, "page_content_html": null, "action_label": null, "is_test": false}'
)

_SKILL_REFINE_PROMPT = (
    "You are a system-prompt engineer. Based on the user's description, craft a concise, "
    "effective system instruction defining an AI persona. Write in English. Max 120 words. "
    "Return ONLY the system instruction text — no quotes, no explanation, no preamble."
)

_WEEKLY_SUMMARY_PROMPT = (
    "Jsi AI tutor pro středoškolské studenty. Analyzuj výsledky studenta za uplynulý týden "
    "a vytvoř personalizované shrnutí v češtině.\n\n"
    "Zahrň:\n"
    "1. Celkové hodnocení výkonu tohoto týdne (pochval za dobré výsledky)\n"
    "2. Identifikaci slabých míst (předměty nebo témata s horší známkou ≥ 3)\n"
    "3. Konkrétní studijní plán na příští týden\n\n"
    "Pokud má student průměrnou známku horší než 3, nebo dostal čtyřku či pětku, nastav "
    "poor_performance=true a přidej výzvu k akci: zeptej se, zda chce vygenerovat "
    "studijní stránku pro problematické téma.\n\n"
    "Odpověz POUZE validním JSON objektem:\n"
    '{"summary": "string (celé shrnutí, 3-5 vět)", '
    '"weak_subjects": ["string"], '
    '"study_plan": "string (konkrétní kroky na příští týden)", '
    '"poor_performance": bool, '
    '"cta": "string nebo null"}'
)

_STUDY_PLAN_PROMPT = (
    "Jsi AI studijní plánovač pro středoškolské studenty. Na základě rozvrhu, "
    "domácích úkolů a slabých předmětů vytvoř realistický studijní plán "
    "pro nadcházející dny v češtině.\n\n"
    "Prioritizuj:\n"
    "1. Úkoly s nejbližším termínem odevzdání\n"
    "2. Opakování slabých předmětů (průměr ≥ 3)\n"
    "3. Přípravu na předměty z rozvrhu\n\n"
    "Navrhni konkrétní studijní bloky (den + aktivita) do volných oken po škole. "
    "Předpokládej cca 1,5–2 hodiny studia denně a buď realistický.\n\n"
    "Odpověz POUZE validním JSON objektem:\n"
    '{"plan": "string (celý plán, 5-10 řádků s konkrétními bloky)", '
    '"priority_tasks": ["string (max 5 nejdůležitějších úkolů)"], '
    '"study_slots": "string (kdy má student největší prostor na studium)", '
    '"tip": "string nebo null"}'
)

_DAILY_SUMMARY_PROMPT = (
    "Jsi AI tutor pro středoškolské studenty. Analyzuj dnešní výsledky studenta "
    "a vytvoř personalizované shrnutí dne v češtině.\n\n"
    "Zahrň:\n"
    "1. Co student dnes dostal za hodnocení (pochval za dobré výsledky)\n"
    "2. Identifikaci slabých míst (předměty s horší známkou ≥ 3)\n"
    "3. Konkrétní tip na dnešní večerní přípravu\n\n"
    "Pokud student dnes dostal čtyřku či pětku, nastav poor_performance=true "
    "a přidej výzvu k akci: zeptej se, zda chce vygenerovat studijní stránku.\n\n"
    "Odpověz POUZE validním JSON objektem:\n"
    '{"summary": "string (shrnutí dne, 2-4 věty)", '
    '"weak_subjects": ["string"], '
    '"study_plan": "string (konkrétní tip na dnešní večer)", '
    '"poor_performance": bool, '
    '"cta": "string nebo null"}'
)

# ── Python-side intent signals ────────────────────────────────────────────────

_GRADE_KEYWORDS = frozenset([
    "známky", "známku", "známek", "průměr", "hodnocení", "dostal jsem",
    "dostala jsem", "bakalář", "bakaláři", "marks", "grades", "grade",
    "špatná známka", "lepší průměr",
])

_MODIFICATION_KEYWORDS = frozenset([
    "přidej", "přidejte", "uprav", "upravit", "uprav to", "změň", "změnit",
    "udělej", "udělej to", "vylepši", "více otázek", "přidej otázky",
    "méně textu", "modernější", "moderní design", "moderní", "make it",
    "zaměř se", "zaměř na", "focus on", "add more", "rewrite", "improve",
    "přepiš", "rozšiř", "zjednodušit", "více příkladů",
])

_CONFIRMATION_KEYWORDS = frozenset([
    "ano", "jo", "yes", "jasně", "ok", "okay", "prosím", "chci",
    "vytvoř", "vytvoř stránku", "studijní stránku", "live page", "create page",
    "vytvořit stránku", "chci stránku",
])

_CONFIRMATION_SENTINEL = "Vidím tvoje známky"

# Max turns kept per user in conversation_history
_HISTORY_KEEP = 40
# Turns sent to the model as context
_HISTORY_CONTEXT = 20

# ── Proactive search detection ────────────────────────────────────────────────

_FACTUAL_KEYWORDS_RE = re.compile(
    r"(píseň|písn[iíě]|písničk\w+|text\s+písn\w*|lyrics?\b|skladb\w+|refrén\w*"
    r"|slova\s+písn\w*|básn[iíě]\w*|básničk\w+|citát\w*|recept\w*"
    r"|naučit\s+se?\s+písn\w*|naučení\s+písn\w*|zpěvník\w*|\bsong\b"
    r"|text\s+od\s+\w+|slova\s+od)",
    re.IGNORECASE,
)

_QUERY_NOISE_RE = re.compile(
    r"\b(udělej|vytvoř|napiš|mi|stránku?|stranku?|pro(?!\s+\w+\s+od)|naučení|naučit"
    r"|chci|potřebuji|prosím|please|o\s+tom|k\s+tomu|ohledně|něco"
    r"|stránk[ay]|page|quiz|kvíz|studijní|vzdělávac[íi]|materiál)\b",
    re.IGNORECASE,
)


def _detect_factual_request(user_input: str) -> "tuple[bool, str]":
    """Return (should_search, search_query) for proactive pre-search."""
    if not _FACTUAL_KEYWORDS_RE.search(user_input):
        return False, ""
    # Strip instruction-style words to get a clean search query
    query = _QUERY_NOISE_RE.sub(" ", user_input)
    query = " ".join(query.split()).strip()
    return True, query[:200]


# ── OpenRouter fallback ───────────────────────────────────────────────────────

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _is_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(t in msg for t in ("429", "quota", "rate_limit", "resource_exhausted", "too many request"))


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
    body: dict = {"model": model, "messages": messages}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if max_tokens:
        body["max_tokens"] = max_tokens

    prompt_chars = sum(len(m["content"]) for m in messages)
    history_turns = len(history) if history else 0
    print(f"[OR] → model={model} json_mode={json_mode} turns={history_turns} prompt_chars={prompt_chars} max_tokens={max_tokens or '∞'}")

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
    content = raw["choices"][0]["message"]["content"]
    tokens_in  = (raw.get("usage") or {}).get("prompt_tokens", "?")
    tokens_out = (raw.get("usage") or {}).get("completion_tokens", "?")
    print(f"[OR] ← {elapsed:.2f}s  tokens={tokens_in}→{tokens_out}  response_chars={len(content)}")
    return content


class GeminiService:
    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set")
        self._client = genai.Client(api_key=api_key)
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

        print(f"[AI REQUEST] user={user_id[:8] if user_id else '?'} model={effective_model} group={model_info['group']}")

        # ── Freemium OpenRouter model: bypass Gemini budget, go direct ───────
        if model_info["group"] == "freemium" and model_info["provider"] == "openrouter":
            json_mode = getattr(config, "response_mime_type", "") == "application/json"
            # Thinking mode: full history + no token cap. Normal: trimmed + capped.
            if ai_mode == AI_MODE_THINKING:
                or_history   = history or []
                or_max_tokens = None
                print(f"[OR] mode=thinking — full history {len(or_history)} turns, unlimited tokens")
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
            print(f"[SEARCH proactive] '{proactive_query}'")
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

    # Fallback: treat the whole response as a plain-text chat message
    log.warning("_parse_ai_response: falling back to plain-text wrap, first 120 chars: %s", s[:120])
    return {
        "message": text,
        "intent": "chat",
        "page_title": None,
        "page_content_html": None,
        "action_label": None,
        "is_test": False,
    }


def _chat_reply(message: str) -> dict:
    return {
        "message": message,
        "intent": "chat",
        "page_title": None,
        "page_content_html": None,
        "action_label": None,
        "is_test": False,
    }


# ── Skill DB helpers ──────────────────────────────────────────────────────────

def has_pending_skill(user_id: str) -> bool:
    return _get_pending_skill(user_id) is not None


def _get_pending_skill(user_id: str) -> "dict | None":
    try:
        with get_connection() as db:
            row = db.execute(
                "SELECT step, data_json FROM pending_skills WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row:
            return {"step": row["step"], "data": json.loads(row["data_json"] or "{}")}
    except Exception:
        log.warning("_get_pending_skill failed for user=%.8s", user_id)
    return None


def _set_pending_skill(user_id: str, step: int, data: dict) -> None:
    try:
        with get_connection() as db:
            db.execute(
                "INSERT INTO pending_skills (user_id, step, data_json, updated_at) "
                "VALUES (?, ?, ?, datetime('now')) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "  step = excluded.step, "
                "  data_json = excluded.data_json, "
                "  updated_at = excluded.updated_at",
                (user_id, step, json.dumps(data, ensure_ascii=False)),
            )
    except Exception:
        log.warning("_set_pending_skill failed for user=%.8s", user_id)


def _clear_pending_skill(user_id: str) -> None:
    try:
        with get_connection() as db:
            db.execute("DELETE FROM pending_skills WHERE user_id = ?", (user_id,))
    except Exception:
        log.warning("_clear_pending_skill failed for user=%.8s", user_id)


def _get_skill(user_id: str, name: str) -> "str | None":
    try:
        with get_connection() as db:
            row = db.execute(
                "SELECT description FROM skills WHERE user_id = ? AND name = ?", (user_id, name)
            ).fetchone()
        return row["description"] if row else None
    except Exception:
        log.warning("_get_skill failed: user=%.8s name=%s", user_id, name)
    return None


def _list_skills(user_id: str) -> list:
    try:
        with get_connection() as db:
            rows = db.execute(
                "SELECT name FROM skills WHERE user_id = ? ORDER BY name", (user_id,)
            ).fetchall()
        return [{"name": r["name"]} for r in rows]
    except Exception:
        log.warning("_list_skills failed for user=%.8s", user_id)
    return []


def _save_skill(user_id: str, name: str, description: str) -> None:
    with get_connection() as db:
        db.execute(
            "INSERT INTO skills (user_id, name, description) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, name) DO UPDATE SET "
            "  description = excluded.description, created_at = datetime('now')",
            (user_id, name, description),
        )


def _delete_skill(user_id: str, name: str) -> bool:
    try:
        with get_connection() as db:
            db.execute("DELETE FROM skills WHERE user_id = ? AND name = ?", (user_id, name))
        return True
    except Exception:
        log.warning("_delete_skill failed: user=%.8s name=%s", user_id, name)
        return False
