"""Příkazy pro provoz: `flask --app wsgi cleanup` (denně z timeru) a `rotate-keys`."""

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import select

from bakix.extensions import db
from bakix.models import Account
from bakix.services import accounts, ai_client, crypto, privacy


@click.command("cleanup")
@with_appcontext
def cleanup() -> None:
    """Smaže neaktivní účty a staré počty volání AI (lhůty ze zásad ochrany soukromí)."""
    removed = accounts.purge_inactive(current_app.config["INACTIVE_ACCOUNT_DAYS"])
    usage = ai_client.purge_usage(privacy.AI_USAGE_RETENTION_DAYS)
    click.echo(f"Smazáno: {removed} neaktivních účtů, {usage} záznamů o volání AI.")


@click.command("rotate-keys")
@with_appcontext
def rotate_keys() -> None:
    """Přešifruje uložené přístupy nejnovějším klíčem z CREDENTIALS_KEY.

    Postup rotace: nový klíč přidej na začátek (NOVY,STARY), spusť tenhle příkaz
    a potom starý klíč odeber."""
    done = failed = 0
    for account in db.session.scalars(select(Account)):
        try:
            account.secrets = crypto.rotate(account.secrets)
            done += 1
        except crypto.DecryptError:
            failed += 1
    db.session.commit()
    click.echo(f"Přešifrováno: {done}, nešlo rozšifrovat: {failed}.")
