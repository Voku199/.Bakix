# Bakix

AI studijní asistent pro české studenty napojený na školní systém **Bakaláři**.

> Vznikl na Vibecoding Hackathonu — 19. 5. 2026

---

## Co to dělá

Bakix se přihlásí do Bakalářů za tebe, stáhne tvoje data (známky, rozvrh, úkoly, Komens zprávy) a předá je AI asistentovi. Ten ti pomáhá studovat — generuje studijní stránky s kvízy, vysvětluje látku, posílá push notifikace a každý týden shrne tvoje výsledky. Přes léto přepne do prázdninového módu a každé pondělí tě informuje, kolik dní zbývá do školy.

---

## Funkce

| Oblast | Co umí |
|--------|--------|
| **Bakaláři** | Přihlášení, známky, rozvrh, úkoly, Komens zprávy, suplování |
| **AI chat** | Google Gemini (fallback OpenRouter), více chatů s historií |
| **Studijní stránky** | AI generuje HTML stránky s kvízy, LaTeXem (KaTeX) a flashcards |
| **Skilly** | Vlastní AI persony — vytvoříš chatovým příkazem `/skill create` |
| **Push notifikace** | Večerní rozvrh (18:00), týdenní shrnutí (neděle 8:00), nové známky, Komens, suplování, prázdninový odpočet |
| **Prázdninový mód** | Červenec/srpen — dashboard zobrazí countdown do školy, scheduler přeskočí zbytečné API volání |
| **Bakix Wrap** | Přehled statistik za pololetí (červen + prosinec) |
| **Kalkulačka průměru** | Spočítá kolik jedniček potřebuješ na vysvědčení |
| **Premium** | 50 AI dotazů/den, neomezené chaty a stránky, Thinking mode, Pro modely |
| **Cache** | AI odpovědi (1 h TTL), Bakaláře API (per-request) — žádné zbytečné volání |
| **HTTPS** | Volitelné přes vlastní certifikát v dev módu |

---

## Architektura

```
app.py                        ← entry point (dev: 9995, prod: waitress 9994)
app/
  __init__.py                 ← create_app(), auth gate, registrace blueprintů
  routes/
    auth_routes.py            ← vyhledávání a validace škol
    login.py                  ← přihlašovací flow, demo uživatel
    bakalari_routes.py        ← dashboard, AI chat, studijní stránky, skilly, wrap
    push.py                   ← správa Web Push subscriptions
    push_routes.py            ← debug push endpointy
    payment_routes.py         ← Stripe checkout + webhook
    proxy_routes.py           ← proxy pro Bakaláře API endpointy
  services/
    bakalari.py               ← BakalariService — celé REST API Bakalářů
    gemini_service.py         ← GeminiService — AI, skilly, studijní stránky
    push_service.py           ← PushNotificationService — Web Push (VAPID)
    scheduler.py              ← APScheduler — všechny background joby
    crypto.py                 ← AES šifrování credentials
    weekly_summary.py         ← logika týdenního shrnutí
    wrap_service.py           ← statistiky pro Bakix Wrap
    payment_service.py        ← Stripe platební logika
    demo_data.py              ← data pro demo uživatele
  database/
    schema.py                 ← init_db(), CREATE TABLE, migrace
    db.py                     ← fetch_row, update_tokens, cache_get/set, …
    connection.py             ← get_connection(), DB_PATH
```

### Auth flow

1. Uživatel zadá URL školy a přihlašovací údaje na `/onboarding`.
2. Bakix ověří, že URL je skutečná Bakalářská instance (detekce `ApiVersion` v API).
3. Přihlašovací údaje se **šifrují AES** a uloží do SQLite — heslo nikdy neleží v plaintextu.
4. Při každém požadavku na Bakaláře se použije uložený access token, nebo proběhne automatický reauth ze šifrovaných credentials.
5. Flask session platí 30 dní (klouzavý TTL). Všechny cesty mimo `/welcome`, `/login`, `/onboarding` jsou chráněné auth gate.

### Jak funguje AI chat

`GeminiService` rozpoznává záměr zprávy v tomto pořadí:

1. **Modifikace stránky** — uživatel chce upravit existující studijní stránku (`přidej`, `uprav`, `změň` …) → přímá editace HTML přes AI
2. **Potvrzení generování** — model čekal na souhlas a uživatel říká ano → vygeneruje stránku
3. **Dotaz na známky** — zpráva obsahuje slova jako `průměr`, `opravná`, `dostala jsem` … → nabídne vytvoření studijní stránky
4. **Obecný chat** → standardní odpověď

Konverzace se ukládají do SQLite (`conversation_history`, max 40 turns) a přežijí reload stránky. Odpovědi se cachují 1 hodinu (klíč = SHA-256 z `user_id + prompt`). Pokud Gemini vrátí 429, automatický fallback na OpenRouter.

---

## Background joby (APScheduler)

| Job | Kdy | Co dělá |
|-----|-----|---------|
| `bakalari_poll` | každou minutu (adaptivní interval 3–30 min) | nové úkoly, komens, známky, suplování — v červenci/srpnu jen známky |
| `evening_reminder` | každý den 18:00 | push se zítřejším rozvrhem |
| `weekly_summary` | neděle 8:00 | týdenní AI shrnutí výsledků |
| `cache_cleanup` | každý den 4:00 | smaže zastaralé cache záznamy (>7 dní) |
| `wrap_push` | 1. června a 1. prosince 9:00 | push notifikace na Bakix Wrap |
| `summer_countdown` | každé pondělí v červenci/srpnu 9:00 | push s odpočtem dní do školy |
| `token_keeper` | každou středu v červenci/srpnu 3:00 | obnoví Bakaláře tokeny všem uživatelům, aby se nemuseli v září znovu přihlašovat |

Adaptivní polling interval: 3 min (školní hodiny), 5 min (odpoledne), 10 min (víkend den), 30 min (noc/víkend noc).

---

## Premium

Jednorázová platba přes **Stripe Checkout** (sandbox i produkce). Výchozí cena 50 Kč / 30 dní.

| | Free | Premium |
|--|------|---------|
| AI dotazy/den | 5 | 50 |
| Chaty | 3 | neomezeno |
| Studijní stránky | 3 | neomezeno |
| Skilly | 1 | neomezeno |
| Thinking mode | — | ✓ |
| Pro modely (Gemini 3.x) | — | ✓ |

Platební flow: `POST /api/payment/checkout` → Stripe hosted page → redirect na `/payment/success?session_id=...` → idempotentní fulfilment. Volitelný Stripe webhook jako fallback (`/api/payment/webhook`).

Testovací karta: `4242 4242 4242 4242`, libovolné datum a CVV.

---

## Instalace

```bash
cd .Bakix
pip install -r requirements.txt
```

Zkopíruj `.env.example` na `.env` a vyplň hodnoty (viz sekce Konfigurace):

```bash
python app.py
```

Dev server: `http://0.0.0.0:9995` (nebo HTTPS, pokud jsou nastaveny certifikáty).
Produkce: automaticky použije `waitress` na portu 9994 (`DEBUG=False`).

### HTTPS v dev módu (volitelné)

```bash
mkdir .certs
openssl req -x509 -newkey rsa:4096 -keyout .certs/dev-key.pem \
  -out .certs/dev-cert.pem -days 365 -nodes -subj "/CN=localhost"
```

### VAPID klíče pro Web Push

```bash
python -c "
from py_vapid import Vapid
v = Vapid()
v.generate_keys()
print('PRIVATE:', v.private_key_b64url)
print('PUBLIC: ', v.public_key_b64url)
"
```

---

## Konfigurace (`.env`)

Zkopíruj `.env` a vyplň proměnné. Šablona je v `.env.example` (pokud existuje), jinak viz tabulka níže.

| Proměnná | Popis |
|----------|-------|
| `SECRET_KEY` | Flask session klíč a AES klíč pro šifrování credentials — v produkci povinně náhodný |
| `DEBUG` | `True` = dev server + `/login/now` endpoint + debug UI. `False` = waitress |
| `TEST` | Skryje Stripe platební sekci z UI |
| `GEMINI_API_KEY` | Google AI Studio — [aistudio.google.com](https://aistudio.google.com) |
| `GEMINI_MODEL` | Výchozí model; per-request přepínání funguje z UI |
| `OPENROUTER_API_KEY` | Fallback při Gemini 429 |
| `VAPID_*` | Klíče pro Web Push (base64url, bez paddingu) |
| `STRIPE_WEBHOOK_SECRET` | Volitelné — bez něj funguje fulfilment přes redirect URL |
| `BAKALARI_INSECURE_SSL` | `true` jen pokud tvoje škola má self-signed certifikát |

---

## Databáze

SQLite se vytvoří automaticky v `instance/bakix.db` při prvním spuštění. Schéma se migruje automaticky (přidávání sloupců bez DROP).

| Tabulka | Co ukládá |
|---------|-----------|
| `saved_credentials` | Šifrované credentials, tokeny, nastavení, Premium stav |
| `conversations` | Seznam chatů (název, timestamp) |
| `conversation_history` | Chat zprávy (max 40 turns na konverzaci) |
| `gemini_cache` | Cachované AI odpovědi (TTL 1 h) |
| `api_cache` | Obecná API cache (Bakaláře data) |
| `push_subscriptions` | Web Push endpointy + VAPID klíče |
| `skills` | Vlastní AI persony uživatelů |
| `pending_skills` | Stav průvodce tvorbou skilu |
| `generated_pages` | AI vygenerované studijní stránky (HTML) |
| `payments` | Stripe audit trail (idempotentní fulfilment) |
| `ai_usage_log` | Počty AI requestů pro rate-limit |
| `activity_log` | Uživatelské události (pro Bakix Wrap statistiky) |

---

## Vývoj

```bash
# Spuštění v debug módu
DEBUG=True python app.py

# Auto-login (přeskočí přihlašovací stránku)
# Nastav AUTO_LOGIN_URL + AUTO_LOGIN_USER v .env, pak otevři:
# GET /login/now

# Testování push notifikací
# Na dashboardu v DEBUG módu je panel "Push — debug" se třemi tlačítky
```

Bakaláře API je dokumentované na [bakalari.cz/api/swagger](https://bakalari.cz/api/swagger) (interní, vyžaduje přihlášení). `BakalariService` v `app/services/bakalari.py` implementuje OAuth token flow (access + refresh token, automatický reauth).

---

## Prázdninový mód

V červenci a srpnu Bakix automaticky:

- **Zobrazí prázdninový banner** na dashboardu s odpočtem dní do školy a datem zahájení nového školního roku
- **Přeskočí polling** rozvrhu, úkolů, Komens zpráv a suplování (školy mají servery prázdné nebo offline)
- **Zachová polling známek** pro studenty s opravnými zkouškami
- **Každé pondělí pošle push** s aktuálním odpočtem ("Do školy zbývá 23 dní")
- **Každou středu obnoví tokeny** všem uživatelům, aby se nemuseli v září znovu přihlašovat

Totéž platí (zkráceně) pro vánoční prázdniny: 23. 12. – 1. 1.
