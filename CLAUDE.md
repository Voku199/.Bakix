# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What Bakix is

AI study assistant for Czech students connected to their school's Bakaláři system. Fetches grades, schedule, homework, and messages via the Bakaláři v3 OAuth API. Generates AI study pages, sends push notifications, and sells a Premium tier via Stripe.

## Running the app

```bash
pip install -r requirements.txt
python app.py
```

- **Dev** (DEBUG=True): HTTP on port 9995; optionally HTTPS if `FLASK_SSL_CERT` + `FLASK_SSL_KEY` are set.
- **Prod** (DEBUG=False): Waitress server on port 9994, 4 threads.

To auto-login on startup (dev only): set `AUTO_LOGIN_URL` + `AUTO_LOGIN_USER` in `.env` and hit `/login/now`.

## Tests

No automated tests currently exist in this repo. If added, an i18n test validating Flask-Babel
translation files (.po/.mo completeness, fuzzy entries, template string coverage, and the
`/set-language/<lang>` route) would be a good first one given the multi-language UI.

After adding new `_('...')` strings, regenerate translations:
```bash
pybabel extract -F babel.cfg -o messages.pot .
pybabel update -i messages.pot -d translations
# edit translations/en/LC_MESSAGES/messages.po
pybabel compile -d translations
```

## Key environment variables

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Flask sessions + credential encryption key (PBKDF2-derived). Changing it invalidates all stored credentials. |
| `DEBUG` | `True` = dev mode (port 9995, debug toolbar, `/login/now`, TEST flag hides Stripe UI) |
| `GEMINI_API_KEY` | Google AI Studio key for AI chat/generation |
| `GEMINI_MODEL` | Defaults to `gemini-2.5-flash-lite`; set to `gemini-2.5-flash` for premium quality |
| `OPENROUTER_API_KEY` | Fallback when Gemini returns 429 |
| `MISTRAL_API_KEY` | Enables the freemium Mistral chat models (services/mistral.py) |
| `VAPID_PRIVATE_KEY` / `VAPID_PUBLIC_KEY` | Web Push (base64url, no padding) |
| `STRIPE_SECRET_KEY` / `STRIPE_PUBLISHABLE_KEY` / `STRIPE_WEBHOOK_SECRET` | Payments |
| `BAKALARI_INSECURE_SSL` | Set to `true` only for schools with self-signed certs |

## Architecture

### Blueprint layout

```
app/__init__.py       create_app(): registers blueprints, inits DB, starts scheduler
app/routes/
  auth_routes.py      /api/schools/search  (municipality API + cache)  /api/validate-school
  login.py            /onboarding  /login  /login/now  /login-demo  /logout
  bakalari_routes.py  Everything after login: dashboard, AI chat, study pages, skills, wrap, settings
  payment_routes.py   /api/payment/checkout  /api/payment/webhook  /payment/success
  proxy_routes.py     Internal proxy: forwards Bakaláře API calls from the client
  push.py             /api/push/subscribe  /api/push/unsubscribe
```

### Service layer

- **`BakalariService`** — Full Bakaláři v3 REST wrapper. OAuth client_id = `"ANDR"`. Token refresh strategy: try refresh_token first, fall back to full re-login from decrypted stored credentials. All data methods follow the same `get_<resource>(access_token) -> dict` pattern.
- **`GeminiService`** — AI chat + HTML study page generation. Detects intent in this order: modify existing page → confirm pending generation → grade-related → general chat. Caches responses 1 hour (key = SHA-256 of user+prompt). On 429 falls back to OpenRouter automatically. Free users: 5 calls/day; premium: 50.
- **`crypto.py`** — AES-256 Fernet with PBKDF2-derived key from `SECRET_KEY`. Used only for credential storage. Never change `SECRET_KEY` in production without migrating credentials.
- **`scheduler.py`** — APScheduler background jobs (Europe/Prague TZ): adaptive Bakaláři polling (3–30 min depending on time of day), evening push at 18:00, weekly AI summary Sundays, summer countdown/token-keeper in Jul–Aug.

### Database

SQLite at `instance/bakix.db`, WAL mode, 30 s timeout. Schema lives in `app/database/schema.py`; `init_db()` runs on every startup and applies non-destructive migrations (only `ADD COLUMN`, never `DROP`).

Key tables: `saved_credentials` (user + encrypted creds + tokens + premium tier), `conversations` + `conversation_history` (chat, max 40 turns), `gemini_cache` (1 h TTL), `api_cache` (Bakaláři responses), `generated_pages` (AI study HTML), `skills` (custom AI personas), `payments` (Stripe audit, `session_id` UNIQUE for idempotency).

### Auth flow

`before_request` gates all routes except `/welcome`, `/onboarding`, `/login*`, `/static`, `/api/*`. Session key is `user_id` (SHA-256 of `school_url:username`). `get_token(user_id)` is the single entry point for getting a valid Bakaláři token — it handles the whole refresh/reauth chain transparently.

### Premium & payments

Stripe Checkout (one-time, 30 days by default via `PREMIUM_DAYS`). Fulfilment happens at `/payment/success` (redirect-based) and optionally at `/api/payment/webhook` (HMAC-verified, CSRF-exempt). Both paths are idempotent — second call on same `session_id` is a no-op.

Free limits (enforced in `bakalari_routes.py`): 3 chats, 3 study pages, 1 skill. Premium removes all limits and unlocks Thinking mode + Pro model.

### i18n

Flask-Babel. Locale from `session["language"]` (set via `/set-language/<lang>` or `POST /api/settings`), falls back to `Accept-Language`, defaults to Czech (`cs`). All user-visible strings use `_('...')`.

### Non-obvious conventions

- **HTML sanitization**: Study page HTML is sanitized with `nh3` before storage and on render — no `<script>`, no event attributes, no `javascript:` hrefs.
- **Logging privacy**: User IDs are always logged as `%.8s` (first 8 hex chars only).
- **Summer mode**: July–August changes dashboard to a countdown banner, skips polling for homework/Komens/timetable (schools go offline), and runs `token_keeper` every Wednesday night to keep tokens alive for September.
- **School search cache**: Municipality list and per-city school lists are cached 24 h in a module-level dict (thread-safe with `threading.Lock`). Cache is in-process only — restarting the app clears it.
