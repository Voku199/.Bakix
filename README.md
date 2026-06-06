# Bakix

Osobní AI asistent pro studenty napojený na český školní systém **Bakaláři**.

> Vzniklý na Vibecoding Hackathonu — 19. 5. 2026

---

## Co to dělá

Bakix se přihlásí do Bakalářů za tebe, stáhne tvoje data (známky, rozvrh, úkoly, Komens) a předá je AI asistentovi, který ti pomáhá studovat — generuje studijní stránky, vysvětluje látku, posílá push notifikace a každý týden posílá shrnutí výsledků.

---

## Funkce

| Oblast | Co umí |
|--------|--------|
| **Bakaláři** | Přihlášení, známky, rozvrh, domácí úkoly, Komens zprávy, suplování |
| **AI chat** | Google Gemini (fallback na OpenRouter), více oddělených chatů s historií (✚ nový chat, 🗂 přepínání), perzistentní napříč reloadem |
| **Studijní stránky** | AI generuje self-contained HTML stránky s kvízem, poznámkami a matematikou v LaTeXu (vykreslení přes KaTeX) pro slabé předměty |
| **Push notifikace** | Večerní připomínka rozvrhu (každý den 18:00), týdenní AI shrnutí (neděle 8:00) |
| **Skilly** | Vlastní AI persony — vytvoříš je chatovým příkazem `/skill create` |
| **Cache** | AI odpovědi se cachují v SQLite (TTL 1 hodina), žádné zbytečné volání API |
| **HTTPS** | Volitelné HTTPS přes vlastní certifikát |
| **Premium** | Odemyká: 50 AI dotazů/den (free 5), režim Přemýšlení, Pro modely (Gemini 3.x), neomezené chaty (free 3), studijní stránky (free 3) a skilly (free 1) |

---

## Jak to funguje

### Přihlášení a autentizace

1. Uživatel na stránce `/onboarding` zadá URL své školy a přihlašovací údaje do Bakalářů.
2. Bakix ověří, že URL je skutečná Bakalářská instance (detekce `ApiVersion` v API).
3. Přihlašovací údaje se **šifrují AES** a uloží do SQLite — heslo v plaintextu nikde neleží.
4. Při každém požadavku na Bakaláře se použije uložený access token, nebo se provede automatické re-přihlášení z šifrovaných credentials.
5. Flask session má životnost 8 hodin; všechny cesty kromě `/welcome`, `/login`, `/onboarding` jsou chráněné auth gate.

### Architektura (Flask blueprinty)

```
app.py                      ← entry point, port 5050
app/
  __init__.py               ← create_app(), auth gate, registrace blueprintů
  routes/
    auth_routes.py          ← registrace, nastavení profilu
    login.py                ← přihlašovací flow
    bakalari_routes.py      ← dashboard, známky, rozvrh, úkoly, Komens
    push.py                 ← správa push odběrů
  services/
    bakalari.py             ← BakalariService — celé REST API Bakalářů
    gemini_service.py       ← GeminiService — AI asistent, skilly, studijní stránky
    push_service.py         ← PushNotificationService — Web Push přes VAPID
    scheduler.py            ← APScheduler — večerní připomínka + týdenní shrnutí
    crypto.py               ← AES šifrování credentials
    weekly_summary.py       ← logika týdenního AI shrnutí
  database/
    schema.py               ← init_db(), všechny CREATE TABLE, migrace
    db.py                   ← fetch_row, update_tokens, …
    connection.py           ← get_connection(), DB_PATH
```

### AI — jak funguje chat

`GeminiService` rozpoznává záměr zprávy v tomto pořadí:

1. **Modifikace** — uživatel chce upravit existující studijní stránku (`přidej`, `uprav`, `změň` …) → okamžitá úprava HTML přes AI
2. **Potvrzení** — model čekal na odpověď a uživatel říká ano → vygeneruje stránku
3. **Dotaz na známky** — zpráva obsahuje slova jako `průměr`, `dostala jsem` … → nabídne vytvoření studijní stránky
4. **Obecný chat** → standardní odpověď

Celá konverzace se ukládá do SQLite (`conversation_history`) — kontext přežívá reload stránky. Odpovědi se cachují 1 hodinu (klíč = SHA-256 z `user_id + prompt`).

Pokud Gemini vrátí chybu 429 (quota), Gemini Service se transparentně přepne na OpenRouter.

### Push notifikace

- Subscription se uloží do `push_subscriptions` (endpoint + VAPID klíče).
- APScheduler spustí každý den v **18:00** job, který pro každého uživatele s aktivní subscriptí stáhne zítřejší rozvrh a pošle push notification.
- Každou **neděli v 8:00** se spustí týdenní AI shrnutí výsledků.
- Pokud endpoint vrátí 404/410, subscription se automaticky smaže.

---

## Instalace a spuštění

```bash
cd .Bakix
pip install -r requirements.txt
```

Zkopíruj `.env` a vyplň hodnoty (viz sekce Konfigurace):

```bash
python app.py
```

Aplikace běží na `http://0.0.0.0:5050` (nebo HTTPS, pokud jsou nastaveny certifikáty).

### Vygenerování dev certifikátů (volitelné)

```bash
mkdir .certs
openssl req -x509 -newkey rsa:4096 -keyout .certs/dev-key.pem \
  -out .certs/dev-cert.pem -days 365 -nodes -subj "/CN=localhost"
```

---

## Konfigurace (`.env`)

| Proměnná | Popis |
|----------|-------|
| `SECRET_KEY` | Flask session klíč — v produkci nastav náhodný řetězec |
| `DEBUG` | `True` / `False` |
| `FLASK_SSL_CERT` | Cesta k SSL certifikátu (volitelné) |
| `FLASK_SSL_KEY` | Cesta k SSL klíči (volitelné) |
| `AUTO_LOGIN_URL` | URL školy pro automatické přihlášení v debug módu |
| `AUTO_LOGIN_USER` | Uživatelské jméno pro auto-login |
| `GEMINI_API_KEY` | Google Gemini API klíč |
| `GEMINI_MODEL` | Výchozí Gemini model (default `gemini-3.1-flash-lite`); konkrétní model lze přepnout i per-request z UI, viz registr `_MODELS` v `gemini_service.py` |
| `OPENROUTER_API_KEY` | Fallback API klíč pro OpenRouter |
| `BAKALARI_INSECURE_SSL` | `true` vypne ověřování TLS certifikátu Bakalářů — **jen pro lokální dev** se self-signed certem; v produkci nechat nenastavené |
| `VAPID_PRIVATE_KEY` | VAPID privátní klíč (base64url) pro Web Push |
| `VAPID_PUBLIC_KEY` | VAPID veřejný klíč |
| `VAPID_MAILTO` | Kontaktní e-mail pro VAPID |

---

## Databáze

SQLite soubor se vytvoří automaticky v `instance/` při prvním spuštění.

| Tabulka | Co ukládá |
|---------|-----------|
| `saved_credentials` | Šifrované přihlašovací údaje + tokeny |
| `conversations` | Seznam chatů uživatele (více oddělených konverzací, jako v ChatGPT) |
| `conversation_history` | Chat historie (max 40 turns na konverzaci, sloupec `conversation_id`) |
| `gemini_cache` | Cachované AI odpovědi (1 hodina TTL) |
| `push_subscriptions` | Web Push endpointy a VAPID klíče |
| `skills` | Vlastní AI persony uživatelů |
| `pending_skills` | Stav průvodce tvorbou skilu |
| `api_cache` | Obecná API cache |
| `generated_pages` | AI vygenerované studijní stránky (dřív soubory v `instance/generated/`, migrují se automaticky při startu) |
| `payments` | Audit trail Stripe plateb (idempotentní fulfilment) |
| `ai_usage_log` | Záznamy AI requestů pro rate-limit účtování |

---

## Závislosti

```
flask
requests
cryptography
python-dotenv
google-genai
pywebpush
APScheduler
waitress
Flask-Babel
Flask-WTF        # CSRF ochrana
Flask-Limiter    # rate-limiting
nh3
ddgs
stripe
```
