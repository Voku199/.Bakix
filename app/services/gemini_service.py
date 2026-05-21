import datetime
import hashlib
import json
import logging
import os

from google import genai
from google.genai import types

from app.database.connection import get_connection

log = logging.getLogger(__name__)

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
    "Zachovej inline CSS styly a celkovou strukturu původního obsahu."
)

_MODIFY_PROMPT = (
    "Jsi AI editor vzdělávacích HTML stránek s přístupem k historii konverzace. "
    "Uprav HTML stránku přesně podle aktuálního požadavku. "
    "Zohledni předchozí instrukce z kontextu (např. moderní design, zaměření na konkrétní předmět). "
    "Vrať POUZE čisté HTML tělo (bez <html>/<head>/<body> tagů, bez markdown bloků, bez komentářů). "
    "Zachovej inline CSS a celkovou strukturu, pokud ji požadavek explicitně nemění."
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
    "1. Při generování HTML vytvoř KOMPLETNÍ materiál: (a) výklad látky, "
    "(b) kvíz s min. 3 otázkami (radio/checkbox), (c) sekce pro poznámky (textarea).\n"
    "2. HTML musí být self-contained — inline CSS, žádné externí závislosti.\n"
    "3. Kvíz: tlačítko 'Zkontrolovat odpovědi' s inline JS (žádné fetch).\n"
    "4. Nastav is_test=true pokud zpráva obsahuje 'test', 'prověrka' nebo 'kvíz'.\n\n"

    "FORMÁT ODPOVĚDI — vrať POUZE validní JSON:\n"
    '  "message"           – odpověď česky (1-3 věty),\n'
    '  "intent"            – "chat" nebo "create_page",\n'
    '  "page_title"        – název stránky (jen pro create_page, jinak null),\n'
    '  "page_content_html" – HTML tělo bez wrapper tagů (jen pro create_page, jinak null),\n'
    '  "action_label"      – text tlačítka (jen pro create_page, jinak null),\n'
    '  "is_test"           – true/false.\n\n'

    "HTML STRUKTURA:\n"
    "<article style='font-family:monospace;max-width:680px;margin:0 auto;line-height:1.7'>\n"
    "  <h1>NADPIS</h1>\n"
    "  <section><!-- výklad --></section><hr>\n"
    "  <section id='quiz'><!-- <div class='q'><label><input type='radio'>...</label></div> --></section>\n"
    "  <button onclick='checkQuiz()'>Zkontrolovat odpovědi</button><hr>\n"
    "  <section id='notes'><h2>Moje poznámky</h2>"
    "<textarea style='width:100%;min-height:80px'></textarea></section>\n"
    "  <script>function checkQuiz(){}</script>\n"
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


class GeminiService:
    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set")
        self._client = genai.Client(api_key=api_key)
        self._model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
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

    # ── Persistent conversation history ───────────────────────────────────────

    def _load_history(self, user_id: str) -> "list[dict]":
        """Return the last _HISTORY_CONTEXT turns as [{"role": ..., "text": ...}]."""
        try:
            with get_connection() as db:
                rows = db.execute(
                    "SELECT role, content FROM conversation_history "
                    "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                    (user_id, _HISTORY_CONTEXT),
                ).fetchall()
            return [{"role": r["role"], "text": r["content"]} for r in reversed(rows)]
        except Exception:
            log.warning("_load_history failed for user=%.8s", user_id)
            return []

    def _save_exchange(self, user_id: str, user_content: str, model_content: str) -> None:
        """Persist one user turn + one model turn, then prune the table."""
        try:
            with get_connection() as db:
                db.execute(
                    "INSERT INTO conversation_history (user_id, role, content) VALUES (?, 'user', ?)",
                    (user_id, user_content),
                )
                db.execute(
                    "INSERT INTO conversation_history (user_id, role, content) VALUES (?, 'model', ?)",
                    (user_id, model_content),
                )
                # Keep only the most recent _HISTORY_KEEP turns per user
                db.execute(
                    "DELETE FROM conversation_history WHERE user_id = ? AND id NOT IN "
                    "(SELECT id FROM conversation_history WHERE user_id = ? "
                    " ORDER BY id DESC LIMIT ?)",
                    (user_id, user_id, _HISTORY_KEEP),
                )
        except Exception:
            log.warning("_save_exchange failed for user=%.8s", user_id)

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def get_cached_response(self, user_id: str, prompt: str) -> "str | None":
        """Return a cached response string, or None on miss/stale.

        Key: SHA-256(user_id + prompt) — per-user isolation without relying solely on WHERE.
        Confirmation prompts are never passed here (handled in handle_grades_context).
        """
        query_hash = hashlib.sha256((user_id + prompt).encode()).hexdigest()
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

    def _save_to_cache(self, user_id: str, prompt: str, response: str) -> None:
        query_hash = hashlib.sha256((user_id + prompt).encode()).hexdigest()
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
        user_input: str,
        context_parts: "list[str]",
        config: types.GenerateContentConfig,
    ) -> dict:
        """Load DB history → check cache → call API → save exchange → return dict.

        user_id is mandatory. context_parts are system/data lines prepended to
        the user message — they are NOT stored in conversation_history.
        """
        prompt_text = "\n\n".join(context_parts + ["Zpráva studenta: " + user_input])

        cached = self.get_cached_response(user_id, prompt_text)
        if cached:
            try:
                return json.loads(cached)
            except json.JSONDecodeError:
                pass

        db_history = self._load_history(user_id)
        hist = [
            types.Content(role=h["role"], parts=[types.Part(text=h["text"])])
            for h in db_history
            if h.get("role") in ("user", "model") and h.get("text")
        ]

        chat = self._client.chats.create(model=self._model, config=config, history=hist)
        response = chat.send_message(prompt_text)
        result = json.loads(response.text)

        result_json = json.dumps(result, ensure_ascii=False)
        self._save_exchange(user_id, user_input, result_json)
        self._save_to_cache(user_id, prompt_text, result_json)

        return result

    # ── Grade-context flow ────────────────────────────────────────────────────

    def handle_grades_context(
        self,
        user_id: str,
        grades: list,
        user_input: str,
    ) -> dict:
        """Route a message through the context-aware grade/page flow.

        Decision tree (evaluated in priority order):
          1. Modification request  → call model immediately, no confirmation
          2. Awaiting confirmation  → confirm → generate page | else → normal chat
          3. Grade-related query   → wants page → generate | else → confirmation prompt
          4. Unrelated             → normal chat with grades silently in context
        """
        history = self._load_history(user_id)

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
                return self._call_api(user_id, user_input, context, self._live_page_config)
            except Exception:
                log.exception("handle_grades_context: modification call failed")
                return _error_response()

        # ── 2. Awaiting confirmation ─────────────────────────────────────────
        if self._awaiting_grade_confirmation(history):
            if self._wants_page_now(user_input):
                return self._generate_grade_page(user_id, grades, user_input)
            return self.generate_chat_response(user_id, user_input)

        # ── 3. Grade-related query ───────────────────────────────────────────
        if self._is_grade_related(user_input):
            if self._wants_page_now(user_input):
                return self._generate_grade_page(user_id, grades, user_input)
            confirmation = {
                "message": "Vidím tvoje známky. Chceš, abych vytvořil studijní stránku pro tato témata?",
                "intent": "chat",
                "page_title": None,
                "page_content_html": None,
                "action_label": None,
                "is_test": False,
            }
            # Persist the exchange so _awaiting_grade_confirmation detects state next turn
            self._save_exchange(user_id, user_input, json.dumps(confirmation, ensure_ascii=False))
            return confirmation

        # ── 4. Unrelated query ───────────────────────────────────────────────
        return self.generate_chat_response(user_id, user_input)

    def _generate_grade_page(
        self,
        user_id: str,
        grades: list,
        user_input: str,
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
            self._save_exchange(user_id, user_input, json.dumps(response, ensure_ascii=False))
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
            self._save_exchange(user_id, user_input, json.dumps(response, ensure_ascii=False))
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
            return self._call_api(user_id, synthesised, context, self._live_page_config)
        except Exception:
            log.exception("_generate_grade_page: API call failed")
            return _error_response()

    # ── Stateful page modification ────────────────────────────────────────────

    def modify_page(self, user_id: str, current_html: str, prompt: str) -> str:
        """Apply a modification prompt to page HTML using persistent conversation history.

        Returns updated HTML string. Saves the exchange to conversation_history so
        future modifications remember prior instructions (e.g. 'modern design').
        """
        history = self._load_history(user_id)
        hist = [
            types.Content(role=h["role"], parts=[types.Part(text=h["text"])])
            for h in history
            if h.get("role") in ("user", "model") and h.get("text")
        ]

        full_prompt = "\n\n".join([
            "Aktuální HTML obsah stránky:\n" + current_html,
            "Požadavek na úpravu: " + prompt,
        ])

        chat = self._client.chats.create(
            model=self._model, config=self._modify_config, history=hist,
        )
        new_html = chat.send_message(full_prompt).text

        # Store only the compact user prompt (not the full HTML) to keep history lean
        self._save_exchange(user_id, prompt, f"[page modified: {prompt[:120]}]")

        return new_html

    # ── Core public methods ───────────────────────────────────────────────────

    def get_response(self, user_id: str, user_input: str, student_data: dict = None) -> dict:
        """Primary chat endpoint with mandatory user_id for DB history lookup.

        Queries conversation_history before every API call to maintain context
        across page reloads. Flow: Load History → Check Cache → Call API → Save.
        """
        context = []
        if student_data:
            context.append("Studentova data: " + json.dumps(student_data, ensure_ascii=False))
        try:
            return self._call_api(user_id, user_input, context, self._ai_chat_config)
        except Exception:
            log.exception("GeminiService.get_response failed")
            return _error_response()

    def generate_chat_response(
        self,
        user_id: str,
        user_input: str,
        student_data: dict = None,
    ) -> dict:
        """Alias for get_response — kept for call-site compatibility."""
        return self.get_response(user_id, user_input, student_data)

    def get_proactive_insights(self, data: dict) -> dict:
        try:
            response = self._client.models.generate_content(
                model=self._model,
                config=self._insights_config,
                contents=json.dumps(data, ensure_ascii=False),
            )
            return json.loads(response.text)
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

    def regenerate_page(self, current_html: str, prompt: str, student_data: dict = None) -> str:
        """Stateless page regen — no history. Use modify_page for stateful modification."""
        parts = []
        if student_data:
            parts.append("Studentova data: " + json.dumps(student_data, ensure_ascii=False))
        parts.append("Aktuální HTML obsah:\n" + current_html)
        parts.append("Požadavek studenta: " + prompt)
        config = types.GenerateContentConfig(system_instruction=_REGEN_PROMPT)
        response = self._client.models.generate_content(
            model=self._model,
            config=config,
            contents="\n\n".join(parts),
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
