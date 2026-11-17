# CLAUDE.md

Pokyny pro práci v tomhle repozitáři.

## Co je Bakix

Česká webová aplikace (Flask): přehled dat ze školního systému Bakaláři pro žáky a rodiče.
Cíl je „kouknout a vědět": málo stránek, žádné nastavování, všechno čitelné na telefonu.
Verze 2 je přepis od nuly. Architektura je podle sesterského projektu Knowix, vzhled podle
autorova webu vokudev.cz.

## Příkazy

- Spuštění: `python app.py` (načte `.env`, provede migrace, spustí server; bez `BAKIX_ENV`
  běží vývojový režim, v produkci waitress na `HOST`:`PORT`). Příkaz `flask --app wsgi …`
  a gunicorn mají výchozí prostředí `production`, které vyžaduje `SECRET_KEY`,
  `CREDENTIALS_KEY`, `DATABASE_URL` a `PRIVACY_*`.
- Testy a lint: `pytest`, `ruff check .`, `ruff format --check .` (CI spouští totéž).
- Schéma jen přes Alembic: `flask --app wsgi db migrate -m "..."`, pak `db upgrade`.
  `tests/test_migrations.py` spadne, když se modely a migrace rozejdou.
- Závislosti: `pyproject.toml`, zámek přes
  `uv pip compile pyproject.toml [--group dev] --universal --python-version 3.13 -o requirements[-dev].txt`.

## Vrstvy

`routes/` (tenké, žádné SQL ani HTTP) → `services/` → `models.py`. Chyby jdou do jediného
handleru v `errors.py`. Blueprint `routes/school.py` je registrovaný dvakrát: `app` (data
přihlášeného účtu, `LiveSource`) a `demo` na `/ukazka` (`DemoSource`). Odkazy v jeho šablonách
proto vždy `url_for('.něco')`, v layoutu `url_for(space ~ '.něco')`.

## Pravidla, která se nesmí porušit

**Data a soukromí**
- Heslo do Bakalářů se nikdy neukládá ani neloguje. Použije se jednou v `accounts.login`.
- Školní data (známky, rozvrh, zprávy…) nejdou do databáze. Žijí jen v `services/cache.py`
  (paměť procesu, `SCHOOL_CACHE_SECONDS`). Nová tabulka s daty žáka = změna zásad na
  `/soukromi` (`services/privacy.py`, posuň `UPDATED_ON`).
- Do logu nejde uživatelské jméno, obsah zpráv ani text odpovědi školy/AI. Účet jen jako
  `account.uid[:8]`.
- Do AI ani do zadání pro AI nejde jméno, škola ani třída s písmenem (jen ročník).

**Odchozí požadavky (SSRF)**
- Na adresu školy smí sahat jen `services/bakalari.py`. Adresa musí projít
  `schools.resolve()` (oficiální seznam nebo `BAKALARI_EXTRA_SCHOOLS`).
- Vždy `allow_redirects=False`, jen https, kontrola veřejné IP v `services/net.py`.

**Výstup (XSS)**
- CSP je `default-src 'self'` bez `unsafe-inline`: žádné inline skripty, styly ani `on…=`
  (hlídá `tests/test_design.py`). JS patří do `static/js/app.js`, stránky musí fungovat i bez něj.
- HTML od školy se převádí na text (`school.html_to_text`), odpověď AI jde přes
  `services/richtext.py` (escapuje a skládá jen pár značek). Filtr `|safe` jen na JSON-LD.

**Formuláře a session**
- Každý POST formulář má `csrf_token()` (hlídá test). Odhlášení a mazání jsou POST.
- Session cookie nese jen `Account.session_token`. Výměna tokenu = odhlášení všude.
- Po přihlášení `session.clear()` před zápisem (session fixation).

**SQLite**
- Běží s `BEGIN IMMEDIATE`: otevřená transakce drží zámek pro zápis. Před voláním školy nebo
  AI transakci ukonči (`commit`/`rollback`), viz komentáře v `services/accounts.py`.
- Produkce = 1 worker × N vláken. Cache, zámky obnovy tokenů a rate limit jsou v paměti
  procesu. Víc workerů znamená přesunout je do Redisu.

## Design systém

Styl je tiskovina, ne hračka: patkové nadpisy (Newsreader, váha 400), Geist pro rozhraní,
Geist Mono pro drobné verzálkové popisky a časy. Žádné stíny, barva se používá úsporně.
Hlavní tlačítko je inkoustové s posunutým stínem v barvě `--clay`.

Tvary jsou kulaté ve třech stupních: panely, tabulky a rámy s buňkami mírně
(`--r-panel`, 16 px), tlačítka, pole a hlášky o něco míň (`--r-control`), štítky, volby,
známky a tečky úplně (`--r-pill`). Tip „na čem zapracovat" má vlastní `--r-callout` a je
bez linky, aby mezi panely vynikl. Nová komponenta si poloměr bere z těchhle tokenů, nikdy
číslem; rám s buňkami oddělenými linkou potřebuje k poloměru i `overflow: hidden`.

- Ručně psané CSS v `static/css/app.css` (žádný build). Tokeny: `--bg`, `--paper`, `--sand`,
  `--ink`, `--body`, `--muted`, `--hair`, `--line`, `--accent`, `--clay`, známky `--g1`…`--g5`.
- Palety: `<html data-palette="…">` z `BAKIX_PALETTE`. Seznam je v `bakix/design.py`, barvy
  v CSS (blok `:root[data-palette="…"]`, světlý i tmavý). Paleta mění jen tokeny; komponenty
  nesmí obsahovat barvu natvrdo. Tmavý vzhled přes `prefers-color-scheme`.
- Sady tvarů: `<html data-shape="…">` z `BAKIX_SHAPE` (`kulaty` výchozí, `mix`, `hranaty`),
  stejný princip jako palety. Výchozí kombinace je `voku` + `kulaty`.
- Komponenty: `.btn`, `.card`, `.chip`, `.mark`, `.lesson`, `.tip`, `.kicker`, `.stats`,
  `.features`. Makra v `templates/components/ui.html`.
- Známka je číslo v kroužku a není nikdy jen barva. Plnou barvu má jen pětka.
- Statické soubory přes `asset('…')` (hash v URL, cache na rok). Ikony = sprite
  `static/img/icons.svg` (Lucide); nová ikona = nový `<symbol>`.
- Všechno včetně přepínače palet je vidět na `/styleguide` (jen ve vývoji).

## SEO

Veřejné jsou jen `/`, `/bezpecnost`, `/soukromi`, `/podminky` (seznam `PUBLIC_ENDPOINTS`
v `security.py` a `PAGES` v `services/seo.py`). Všechno ostatní dostane `noindex` a
`no-store`. Veřejná šablona začíná `{% set public_page = True %}` a má vlastní `title`
a `description`. Adresy se skládají z `PUBLIC_URL`, nikdy z hlavičky Host.

## Texty

Česky, tykání, krátce. Žádné „AI asistent, který…" superlativy: popisuj, co stránka dělá.
Rodič i žák čtou stejné stránky; kde se oslovení liší, rozhoduje `profile.is_parent`.
