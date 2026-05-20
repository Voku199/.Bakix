import json
import logging
import os

from google import genai
from google.genai import types

log = logging.getLogger(__name__)

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

_AI_CHAT_PROMPT = (
    "Jsi proaktivní AI vzdělávací asistent pro studenty středních a základních škol. "
    "Vždy odpovídej česky. Buď podporující, konkrétní a akční.\n\n"

    "PRAVIDLA CHOVÁNÍ:\n"
    "1. Pokud student žádá o studijní materiál, test, cvičení nebo stránku — VŽDY použij intent=create_page. "
    "Nikdy neodpovídej jen textem, pokud student žádá o stránku nebo materiál.\n"
    "2. Pokud zpráva obsahuje slovo 'test' nebo 'prověrka' nebo 'kvíz' — nastav is_test=true.\n"
    "3. Při generování HTML vytvoř vždy KOMPLETNÍ materiál s: (a) jasným výkladem látky, "
    "(b) interaktivním kvízem s min. 3 otázkami (radio nebo checkbox vstupy), "
    "(c) sekcí pro vlastní poznámky studenta (textarea s popisem).\n"
    "4. HTML musí být self-contained — žádné externí skripty ani styly. Použij inline CSS.\n"
    "5. Kvíz musí mít tlačítko 'Zkontrolovat odpovědi' které po kliknutí zvýrazní správné/špatné volby "
    "pomocí inline JS (žádné fetch volání).\n\n"

    "FORMÁT ODPOVĚDI — vrať POUZE validní JSON s těmito klíči:\n"
    '  "message"           – tvoje odpověď česky (1-3 věty),\n'
    '  "intent"            – "chat" nebo "create_page",\n'
    '  "page_title"        – název stránky (pouze pokud intent=create_page, jinak null),\n'
    '  "page_content_html" – HTML tělo obsahu bez <html>/<body>/<head> tagů (pouze pokud intent=create_page, jinak null),\n'
    '  "action_label"      – text tlačítka pro otevření (pouze pokud intent=create_page, jinak null),\n'
    '  "is_test"           – true pokud jde o test nebo prověrku, jinak false.\n\n'

    "HTML STRUKTURA pro create_page (použij tuto šablonu):\n"
    "<article style='font-family:monospace;max-width:680px;margin:0 auto;line-height:1.7'>\n"
    "  <h1 style='...'>NADPIS</h1>\n"
    "  <section><!-- výklad látky s <h2>, <p>, <ul> --></section>\n"
    "  <hr>\n"
    "  <section id='quiz'><!-- otázky: každá v <div class='q'>, odpovědi jako <label><input type='radio'> --></section>\n"
    "  <button onclick='checkQuiz()' style='...'>Zkontrolovat odpovědi</button>\n"
    "  <hr>\n"
    "  <section id='notes'>\n"
    "    <h2>Moje poznámky</h2>\n"
    "    <textarea placeholder='Zapiš si klíčové pojmy...' style='width:100%;min-height:80px'></textarea>\n"
    "  </section>\n"
    "  <script>function checkQuiz(){/* zvýrazni správné/špatné */}</script>\n"
    "</article>"
)


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
            return {"alert": "", "recommendation": "", "exercise": "", "chat_prompt": "", "error": "AI insights unavailable"}

    def send_chat_message(self, history: list, message: str) -> str:
        try:
            chat_history = [
                types.Content(role=item["role"], parts=[types.Part(text=item["text"])])
                for item in history
                if item.get("role") in ("user", "model") and item.get("text")
            ]
            chat = self._client.chats.create(
                model=self._model,
                config=self._chat_config,
                history=chat_history,
            )
            response = chat.send_message(message)
            return response.text
        except Exception:
            log.exception("GeminiService.send_chat_message failed")
            return "Omlouvám se, nastala chyba. Zkus to prosím znovu."

    def generate_chat_response(self, user_input: str, history: list, student_data: dict = None) -> dict:
        try:
            parts = []
            if student_data:
                parts.append("Studentova data: " + json.dumps(student_data, ensure_ascii=False))
            parts.append("Zpráva studenta: " + user_input)

            hist = [
                types.Content(role=item["role"], parts=[types.Part(text=item["text"])])
                for item in history
                if item.get("role") in ("user", "model") and item.get("text")
            ]
            chat = self._client.chats.create(
                model=self._model,
                config=self._ai_chat_config,
                history=hist,
            )
            response = chat.send_message("\n\n".join(parts))
            return json.loads(response.text)
        except Exception:
            log.exception("GeminiService.generate_chat_response failed")
            return {
                "message": "Omlouvám se, nastala chyba. Zkus to prosím znovu.",
                "intent": "chat",
                "page_title": None,
                "page_content_html": None,
                "action_label": None,
                "is_test": False,
            }

    def regenerate_page(self, current_html: str, prompt: str, student_data: dict = None) -> str:
        context_parts = []
        if student_data:
            context_parts.append("Studentova data: " + json.dumps(student_data, ensure_ascii=False))
        context_parts.append("Aktuální HTML obsah:\n" + current_html)
        context_parts.append("Požadavek studenta: " + prompt)

        config = types.GenerateContentConfig(system_instruction=_REGEN_PROMPT)
        response = self._client.models.generate_content(
            model=self._model,
            config=config,
            contents="\n\n".join(context_parts),
        )
        return response.text
