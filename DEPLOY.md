# Nasazení

Nejjednodušší cesta je `python app.py` s `BAKIX_ENV=production` v `.env`: spustí waitress na
`HOST`:`PORT` a migrace provede sám. Před něj patří reverzní proxy s HTTPS (Caddy, nginx).
Níž je varianta pro Raspberry Pi se systemd a gunicornem.

## Raspberry Pi

Stejný vzor jako Knowix: gunicorn za Caddy, SQLite, systemd. Soubory jsou v `deploy/pi/`.
Na Pi běží i jiné projekty: na jejich služby a bloky v Caddy se nesahá.

| Co | Kde |
| --- | --- |
| Kód | `/home/voku/bakix-app` |
| Služba | `bakix.service`, uživatel `svc-bakix`, gunicorn na `127.0.0.1:9960` |
| Databáze | SQLite `/var/lib/bakix/bakix.db` (jen účty a kvóty AI) |
| Úklid | `bakix-daily.timer` ve 3:40 (`flask cleanup`) |
| Proměnné | `/etc/bakix/bakix.env` (root:svc-bakix, 640), vzor `deploy/pi/bakix.env.example` |
| Caddy | `/etc/caddy/sites/bakix.cz.caddy` |
| Logy | `journalctl -u bakix` |

## První instalace (na Pi)

```bash
sudo useradd --system --home /var/lib/bakix --shell /usr/sbin/nologin svc-bakix
```

```bash
cd ~/bakix-app && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
sudo install -d -m 750 -o root -g svc-bakix /etc/bakix && sudo install -m 640 -o root -g svc-bakix deploy/pi/bakix.env.example /etc/bakix/bakix.env
```

Vyplň `/etc/bakix/bakix.env` (`SECRET_KEY`, `CREDENTIALS_KEY`, `PRIVACY_*`), pak:

```bash
sudo cp deploy/pi/systemd/bakix* /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now bakix.service bakix-daily.timer
```

```bash
curl -s http://127.0.0.1:9960/healthz
```

Teprve když `/healthz` odpovídá, vyměň blok v Caddy (`deploy/pi/caddy/bakix.cz.caddy`) a
zastav starou verzi. Uživatelé staré verze se přihlásí znovu: nová databáze začíná prázdná
a ze staré se nic nepřevádí (hesla ani konverzace přenášet nechceme).

## Starou databázi smaž

Stará `instance/app.db` obsahuje vratně zašifrovaná hesla a tokeny v čitelné podobě. Po
přepnutí ji i se zálohami a starým `instance/secret_key` smaž.

## Nová verze

```bash
cd ~/bakix-app && git pull && .venv/bin/pip install -r requirements.txt && sudo systemctl restart bakix
```

Migrace databáze proběhnou při startu (`ExecStartPre=flask db upgrade`).

## Rotace šifrovacího klíče

V `/etc/bakix/bakix.env` nastav `CREDENTIALS_KEY=NOVY,STARY`, restartuj, spusť
`flask --app wsgi rotate-keys` (jako `svc-bakix`, se stejnými proměnnými) a starý klíč odeber.
Kdo se mezitím nepřihlásil, přihlásí se znovu.
