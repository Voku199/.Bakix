# Bakix

**Bakaláři na jeden pohled.** Známky, rozvrh, úkoly a zprávy ze školního systému Bakaláři
přehledně na jedné stránce, pro žáky i rodiče. Když se nedaří, Bakix z předmětu a probíraného
tématu sepíše zadání pro AI, která látku vysvětlí.

Verze 2 je přepsaná od nuly (původní Bakix vznikl na Vibecoding Hackathonu 19. 5. 2026).
Bakix není oficiální aplikace Bakalářů.

## Co umí

| Stránka | Co na ní je |
| --- | --- |
| **Přehled** | Jedna věta („Dnes 6 hodin, konec ve 13:30."), dnešní nebo zítřejší rozvrh se změnami, poslední známky, úkoly podle termínu, nepřečtené zprávy |
| **Známky** | Předměty s průměrem a trendem, u každé známky z čeho byla, kalkulačka „co kdyby" |
| **Rozvrh** | Týden se suplováním a odpadlými hodinami |
| **Úkoly**, **Zprávy**, **Absence** | Čitelně i na telefonu, jen pro čtení |
| **Učení** | Zadání pro AI podle předmětu, tématu, známky a ročníku. U angličtiny odkaz na cvičení v [Knowixu](https://knowix.cz) |
| **Ukázka** | Totéž s vymyšlenými daty na `/ukazka`, bez přihlášení a bez cookies |

## Spuštění

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python app.py
```

Na Windows je to `.venv\Scripts\pip` a `.venv\Scripts\python`. Nic dalšího není potřeba:
`app.py` si sám připraví databázi a bez `.env` běží ve vývojovém režimu na
<http://127.0.0.1:5000>. Ukázka je na `/ukazka`, přehled komponent a palet na `/styleguide`.

Server se nastavuje v `.env` (vzor je `.env.example`):

| Proměnná | Co dělá |
| --- | --- |
| `BAKIX_ENV` | `development` (výchozí pro `app.py`) nebo `production` (waitress, vyžaduje klíče) |
| `HOST`, `PORT`, `THREADS` | kde server poslouchá a kolik má vláken |
| `BAKIX_PALETTE` | barevná paleta: `voku` (výchozí), `inkoust`, `tabule`, `grafit`, `svestka` |
| `BAKIX_SHAPE` | tvary: `kulaty` (výchozí), `mix` (hranatý rám, kulaté ovládání), `hranaty` |
| `SECRET_KEY`, `CREDENTIALS_KEY`, `PRIVACY_*` | povinné v produkci |

## Testy a lint

```bash
.venv/bin/pytest
```

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Testy nepotřebují síť: školní server i seznam škol nahrazuje `tests/conftest.py`.

## Jak je to poskládané

```
app.py                  spuštění jedním příkazem (python app.py), nastavení z .env
wsgi.py                 vstupní bod pro gunicorn a příkaz flask
bakix/
  __init__.py           create_app()
  config.py             prostředí: development / testing / production (BAKIX_ENV)
  design.py             seznam barevných palet (BAKIX_PALETTE)
  security.py           CSP a další hlavičky, noindex + no-store pro soukromé stránky
  errors.py             jednotné chybové stránky
  models.py             Account, AiUsage (nic dalšího se neukládá)
  routes/               tenké: public, auth, school (2× – přihlášený a /ukazka), account
  services/
    bakalari.py         HTTP klient Bakaláři API v3
    schools.py          seznam škol = vyhledávání i allowlist
    net.py              jen https, žádná přesměrování, žádné soukromé IP
    accounts.py         přihlášení, session, obnova tokenů, mazání
    crypto.py           Fernet (CREDENTIALS_KEY), rotace klíčů
    sources.py          LiveSource (škola) a rozhraní pro šablony
    demo.py             DemoSource (ukázka)
    school.py           třídy Mark, Subject, Lesson… a parsery odpovědí
    insights.py         pravidla: věta na přehledu, „na čem zapracovat", kalkulačka
    study.py            zadání pro AI, odkazy na Knowix
    ai_client.py        volitelná AI s kvótami
    privacy.py, seo.py  podklady pro /soukromi, sitemap, JSON-LD
  templates/, static/   jeden layout, komponenty v components/ui.html, ručně psané CSS
migrations/             Alembic
tests/                  pytest
deploy/pi/              systemd + Caddy pro Raspberry Pi (viz DEPLOY.md)
```

## Bezpečnost v kostce

- Heslo do Bakalářů se neukládá. Ukládá se jen zašifrovaný token (Fernet, vlastní klíč).
- Známky, rozvrh ani zprávy nejdou do databáze, jen na pár minut do paměti procesu.
- Přihlásit se jde jen do školy z oficiálního seznamu (ochrana proti SSRF).
- CSP `default-src 'self'` bez `unsafe-inline`, nic z cizích domén.
- HTML od školy ani od AI se nikdy nevykresluje.

Podrobně na stránce `/bezpecnost` a v `CLAUDE.md`.

## Licence

MIT (viz `LICENSE`). Písma Newsreader, Geist a Geist Mono pod SIL OFL
(`bakix/static/fonts/`), ikony Lucide pod ISC.
