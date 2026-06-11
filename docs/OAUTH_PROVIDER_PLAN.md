# Plán: Bakix jako poskytovatel identity („Přihlásit se přes Bakix")

Tento dokument popisuje stranu **Bakixu** v integraci „Přihlásit se přes Bakix" pro Knowix.
Protějšek (klientská strana) je popsán v repu `.Bakix-Knowix` v `docs/BAKIX_LOGIN_PLAN.md`.

## Cíl

Knowix uživatel klikne na „Přihlásit se přes Bakix", je přesměrován na Bakix, kde už je
(nebo se) přihlásí svými Bakaláře údaji, odsouhlasí sdílení profilu a vrátí se do Knowixu
přihlášený. Bakix se tím stává malým OAuth 2.0 authorization serverem.

## Zvolený protokol: OAuth 2.0 Authorization Code + PKCE (S256)

Proč zrovna tohle a ne něco jednoduššího:

- **Sdílení session cookie nejde** — `SameSite=Lax` + jiná doména/port, a hlavně by to
  svázalo obě aplikace bezpečnostně dohromady.
- **Implicit flow / token v URL je zakázané** moderními doporučeními (token by protekl
  přes historii prohlížeče, referery, logy).
- **Authorization Code + PKCE** je dnešní standard i pro server-side klienty; PKCE chrání
  proti útoku podvržením/odposlechnutím autorizačního kódu.
- Knowix je *confidential client* (server), takže vyžadujeme **client_secret i PKCE zároveň**.

Identita uživatele (`sub`) = existující Bakix `user_id` (`sha256(school_url:username)`,
64 znaků hex) — je deterministický a stabilní, ideální jako externí identifikátor.
**Nikdy nesdílíme Bakaláře přihlašovací údaje ani Bakaláře tokeny** — jen profil.

## Nové DB tabulky (`app/database/schema.py`)

```sql
oauth_clients (
    client_id          TEXT PRIMARY KEY,      -- secrets.token_urlsafe(24)
    client_secret_hash TEXT NOT NULL,         -- sha256 hex tajemství (plaintext se zobrazí jen 1×)
    name               TEXT NOT NULL,          -- "Knowix" (zobrazuje se na consent obrazovce)
    redirect_uris      TEXT NOT NULL,          -- JSON pole, porovnává se PŘESNOU shodou
    created_at         TEXT DEFAULT (datetime('now'))
);

oauth_codes (
    code_hash      TEXT PRIMARY KEY,           -- sha256(kódu), plaintext kód jen v redirectu
    client_id      TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    redirect_uri   TEXT NOT NULL,              -- musí sedět i při výměně za token
    code_challenge TEXT NOT NULL,              -- PKCE S256 challenge
    scope          TEXT NOT NULL DEFAULT 'profile',
    expires_at     TEXT NOT NULL,              -- now + 60 s
    used_at        TEXT                        -- jednorázovost; reuse = revokace tokenů
);

oauth_tokens (
    token_hash  TEXT PRIMARY KEY,              -- sha256(tokenu), DB nikdy nezná plaintext
    client_id   TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'profile',
    expires_at  TEXT NOT NULL,                 -- now + 1 h (Knowixu stačí na userinfo)
    revoked_at  TEXT
);
```

Refresh tokeny **záměrně nevydáváme** — Knowix potřebuje token jen jednorázově na
`/oauth/userinfo` při přihlášení. Méně stavů = menší útočná plocha.

## Nové endpointy — nový blueprint `app/routes/oauth_provider.py`

### `GET /oauth/authorize`
Parametry: `response_type=code`, `client_id`, `redirect_uri`, `scope=profile`, `state`,
`code_challenge`, `code_challenge_method=S256`.

1. Validace client_id + **přesná shoda** redirect_uri proti registru (žádné wildcardy,
   žádné prefix matche). Při neplatném client_id/redirect_uri **nepřesměrovávat** —
   zobrazit chybovou stránku (jinak open redirect).
2. `code_challenge_method` jiné než `S256` → chyba `invalid_request`. `state` povinný.
3. Nepřihlášený uživatel → redirect na `/login?next=<plné authorize URL>`
   (next validovat jako relativní URL začínající `/oauth/authorize`).
4. **Demo session (`session["is_demo"]`) nesmí autorizovat** → chybová stránka.
5. Zobrazit consent obrazovku (`templates/oauth_consent.html`): název aplikace, co se
   sdílí (jméno, škola, tier), tlačítka Povolit/Zamítnout. Formulář má CSRF token
   (Flask-WTF už je v projektu).

### `POST /oauth/authorize` (odeslání consentu)
- Povolit → vygenerovat kód `secrets.token_urlsafe(32)`, uložit `sha256` s TTL 60 s,
  redirect na `redirect_uri?code=...&state=...`.
- Zamítnout → redirect s `error=access_denied&state=...`.

### `POST /oauth/token`
Server-to-server (volá Knowix backend). `grant_type=authorization_code`, `code`,
`redirect_uri`, `client_id`, `client_secret`, `code_verifier`.

1. Ověřit client_secret (porovnání hashů přes `hmac.compare_digest`).
2. Najít kód podle hashe; zkontrolovat expiraci, `used_at IS NULL`, shodu client_id
   a redirect_uri.
3. **PKCE**: `sha256(code_verifier)` base64url == `code_challenge`.
4. **Detekce replay**: pokud `used_at` už je vyplněné → okamžitě revokovat všechny
   tokeny vydané z tohoto kódu a vrátit `invalid_grant`.
5. Označit kód jako použitý, vydat access token (`secrets.token_urlsafe(32)`, do DB hash,
   TTL 1 h). Odpověď: `{"access_token": ..., "token_type": "Bearer", "expires_in": 3600, "scope": "profile"}`.
- CSRF exempt (stejný vzor jako Stripe webhook v `app/__init__.py`), rate limit
  `10 per minute` (Flask-Limiter už je v projektu).
- Chybové odpovědi dle RFC 6749 (`invalid_client` → 401, `invalid_grant` → 400),
  bez úniku detailů.

### `GET /oauth/userinfo`
`Authorization: Bearer <token>`. Vrací:

```json
{
  "sub": "<bakix user_id, 64 hex>",
  "display_name": "...",
  "school_url": "https://...",
  "subscription_tier": "free|premium"
}
```

Rate limit 30/min. Nikdy nevracet enc_creds, tokeny Bakaláře ani settings.

## Návaznost na existující kód

- `app/__init__.py` — auth-gate `_check_auth` (řádky ~214–224): cesty `/oauth/token`
  a `/oauth/userinfo` přidat mezi výjimky (mají vlastní autentizaci klientem/tokenem);
  `/oauth/authorize` výjimku NEMÁ — tam je session vyžadovaná, jen je potřeba, aby
  redirect na login uměl předat `next`.
- `app/routes/login.py` — `login()` po úspěchu respektovat validovaný `next` parametr
  (jen relativní cesty začínající `/oauth/authorize`, jinak ignorovat → dashboard).
- Registrace klienta: CLI příkaz `flask oauth-client-create "Knowix" <redirect_uri>`
  (v `app/__init__.py` přes `@app.cli.command`), vypíše client_id + secret jednorázově.
- Úklid: APScheduler job (už v projektu) — mazat expirované `oauth_codes`/`oauth_tokens`.

## Bezpečnostní checklist (strana Bakix)

- [ ] PKCE S256 povinné, `plain` odmítnout
- [ ] redirect_uri: přesná shoda, HTTPS v produkci, žádné wildcardy
- [ ] Při neplatném klientovi/redirect_uri se NEPŘESMĚROVÁVÁ
- [ ] `state` povinný a vracený beze změny
- [ ] Kódy: jednorázové, 60 s TTL, v DB jen hash, reuse → revokace tokenů
- [ ] Tokeny: opaque (ne JWT → revokovatelné), v DB jen hash, TTL 1 h
- [ ] client_secret v DB jen jako hash, porovnání constant-time
- [ ] Consent formulář chráněn CSRF (Flask-WTF)
- [ ] Demo účet (`is_demo`) nesmí autorizovat
- [ ] Rate limity: /oauth/token 10/min, /oauth/userinfo 30/min, /oauth/authorize 30/min
- [ ] `next` na loginu: jen relativní URL na /oauth/authorize (žádný open redirect)
- [ ] Žádné tokeny/kódy/secrety v lozích
- [ ] userinfo vrací jen profil, nikdy credentials ani Bakaláře tokeny

## Pořadí prací (celá integrace)

1. **Bakix**: schema + blueprint + consent šablona + CLI registrace klienta (tento dokument)
2. **Knowix**: klientská strana (viz `.Bakix-Knowix/docs/BAKIX_LOGIN_PLAN.md`)
3. **Knowix hardening**: CSRF, rate limiting, security headers (předpoklad bezpečného callbacku)
4. **Testy**: unit testy na token endpoint (expirace, reuse, špatný verifier, špatný secret),
   E2E průchod celým flow v DEBUG režimu na localhostu (Bakix :9991, Knowix :9990)
