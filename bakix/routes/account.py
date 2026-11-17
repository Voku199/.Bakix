"""Účet: co o tobě Bakix ví, odhlášení ze všech zařízení a smazání."""

from flask import Blueprint, flash, redirect, render_template, url_for

from bakix.services import accounts, ai_client, privacy

bp = Blueprint("account", __name__, url_prefix="/ucet")


@bp.before_request
def require_login():
    if accounts.current() is None:
        return redirect(url_for("auth.login"))
    return None


@bp.get("")
def index():
    account = accounts.current()
    return render_template(
        "account/index.html",
        profile=accounts.profile_of(account),
        created_at=account.created_at,
        retention=privacy.retention(),
        ai_enabled=ai_client.is_enabled(),
        ai_remaining=ai_client.remaining_today(account) if ai_client.is_enabled() else 0,
    )


@bp.post("/odhlasit-vsude")
def logout_everywhere():
    accounts.logout_everywhere(accounts.current())
    flash("Odhlášeno ze všech zařízení.", "info")
    return redirect(url_for("public.index"))


@bp.post("/smazat")
def delete():
    accounts.delete_account(accounts.current())
    flash("Účet je smazaný. Bakix si o tobě nic nepamatuje.", "success")
    return redirect(url_for("public.index"))
