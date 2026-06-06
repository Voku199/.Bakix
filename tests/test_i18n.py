"""
Tests for the Flask-Babel i18n pipeline.

Covers:
  1. Translation file health  (.po/.mo existence, key strings translated, no empty entries)
  2. Template coverage        (every _('...') in index.html has an English translation)
  3. Bare-string audit        (card titles must be wrapped in _(), not hard-coded)
  4. Runtime locale selection (_get_locale reads session, rejects invalid values)
  5. Language-switch flow     (/set-language route, POST /api/settings updates session)
"""

import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PO_PATH  = ROOT / "translations" / "en" / "LC_MESSAGES" / "messages.po"
MO_PATH  = ROOT / "translations" / "en" / "LC_MESSAGES" / "messages.mo"
INDEX_HTML = ROOT / "app" / "templates" / "index.html"


# ── shared fixtures ────────────────────────────────────────────────────────────

def _po_catalog() -> dict:
    """Return {msgid: msgstr} for all translated entries in the English .po."""
    from babel.messages.pofile import read_po
    with open(PO_PATH, "rb") as f:
        cat = read_po(f)
    # Catalog iterates as Message objects with .id and .string
    return {str(msg.id): str(msg.string) for msg in cat if msg.id and str(msg.string).strip()}


def _template_msgids(path: Path) -> set:
    """Extract every _('...') and _("...") msgid from a Jinja2 template."""
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"_\(['\"](.+?)['\"]\)", text))


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("SECRET_KEY", "test-i18n-secret")
    os.environ.setdefault("BAKALARI_URL", "http://localhost")
    with patch("app.database.schema.init_db"), \
         patch("app.services.scheduler.start_scheduler"):
        from app import create_app
        a = create_app()
        a.config["TESTING"] = True
        a.config["WTF_CSRF_ENABLED"] = False
        return a


@pytest.fixture()
def client(app):
    return app.test_client()


# ── 1. Translation file health ─────────────────────────────────────────────────

class TestTranslationFiles:
    def test_mo_file_exists(self):
        assert MO_PATH.exists(), ".mo file missing — run: pybabel compile -d translations"

    def test_mo_file_not_empty(self):
        assert MO_PATH.stat().st_size > 200, ".mo file is suspiciously small (corrupt?)"

    def test_mo_newer_than_po(self):
        assert MO_PATH.stat().st_mtime >= PO_PATH.stat().st_mtime, (
            ".mo is older than .po — run: pybabel compile -d translations"
        )

    def test_key_ui_strings_translated(self):
        catalog = _po_catalog()
        expected = {
            "Rozvrh":      "Timetable",
            "Úkoly":       "Tasks",
            "Zprávy":      "Messages",
            "Známky":      "Grades",
            "Nová zpráva": "New message",
            "Příjemce":    "Recipient",
            "Text zprávy": "Message text",
            "Odeslat":     "Send",
            "Uložit":      "Save",
            "Zavřít":      "Close",
            "Zrušit":      "Cancel",
            "Nastavení":   "Settings",
            "Dnes":        "Today",
            "Zítra":       "Tomorrow",
        }
        for msgid, want in expected.items():
            assert msgid in catalog, f"Missing EN translation for: {msgid!r}"
            assert catalog[msgid] == want, (
                f"{msgid!r} → expected {want!r}, got {catalog[msgid]!r}"
            )

    def test_no_untranslated_entries(self):
        """Every msgid in the .po must have a non-empty msgstr."""
        from babel.messages.pofile import read_po
        with open(PO_PATH, "rb") as f:
            cat = read_po(f)
        empty = [str(msg.id) for msg in cat if msg.id and not str(msg.string).strip()]
        assert not empty, "Untranslated (empty msgstr) msgids:\n" + "\n".join(f"  {m}" for m in empty)

    def test_no_fuzzy_entries(self):
        """Fuzzy entries are skipped by Flask-Babel — every entry must be approved."""
        raw = PO_PATH.read_text(encoding="utf-8")
        fuzzy_raw = re.findall(r"#, fuzzy\nmsgid \"(.+?)\"", raw)
        assert not fuzzy_raw, "Fuzzy (unapproved) entries found:\n" + "\n".join(f"  {m}" for m in fuzzy_raw)


# ── 2. Template coverage ───────────────────────────────────────────────────────

class TestTemplateCoverage:
    def test_all_index_msgids_have_translation(self):
        """Every _('...') call in index.html must exist in the English .po."""
        catalog = _po_catalog()
        msgids  = _template_msgids(INDEX_HTML)
        missing = sorted(m for m in msgids if m not in catalog)
        assert not missing, (
            "Strings in index.html without English translation:\n"
            + "\n".join(f"  {m!r}" for m in missing)
        )


# ── 3. Bare-string audit ───────────────────────────────────────────────────────

class TestBareStrings:
    CARD_TITLES = ["Rozvrh", "Úkoly", "Zprávy", "Známky"]

    def test_card_titles_wrapped_in_gettext(self):
        """Card title headings must use {{ _('...') }}, not bare strings."""
        text = INDEX_HTML.read_text(encoding="utf-8")
        for title in self.CARD_TITLES:
            bare = re.search(rf'class="card__title">\s*{re.escape(title)}\s*<', text)
            assert not bare, (
                f'"{title}" is a bare (untranslated) string in index.html — '
                f"wrap it with {{{{ _('{title}') }}}}"
            )

    def test_no_bare_czech_in_card_titles(self):
        """General check: card__title elements must not contain raw Czech text."""
        text = INDEX_HTML.read_text(encoding="utf-8")
        # Match card title content that does NOT contain {{ _( ... ) }}
        for m in re.finditer(r'class="card__title">([^<{]+)<', text):
            content = m.group(1).strip()
            # If it looks like Czech (has diacritics or common Czech words), fail
            if re.search(r'[áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]', content):
                pytest.fail(
                    f'Bare Czech string in card__title: {content!r} — '
                    f"wrap it with {{{{ _('...') }}}}"
                )


# ── 4. Runtime locale selection ────────────────────────────────────────────────

class TestLocaleSelection:
    def test_get_locale_returns_en_from_session(self, app):
        with app.test_request_context("/"):
            from flask import session
            session["language"] = "en"
            from flask_babel import get_locale
            assert str(get_locale()) == "en"

    def test_get_locale_returns_cs_from_session(self, app):
        with app.test_request_context("/"):
            from flask import session
            session["language"] = "cs"
            from flask_babel import get_locale
            assert str(get_locale()) == "cs"

    def test_get_locale_ignores_invalid_session_value(self, app):
        with app.test_request_context("/"):
            from flask import session
            session["language"] = "xx"
            from flask_babel import get_locale
            assert str(get_locale()) in ("cs", "en"), "Invalid locale must not leak through"

    def test_gettext_translates_in_english(self, app):
        from flask_babel import force_locale
        with app.test_request_context("/"):
            with force_locale("en"):
                from flask_babel import gettext as _
                assert _("Rozvrh") == "Timetable"
                assert _("Úkoly") == "Tasks"
                assert _("Zprávy") == "Messages"
                assert _("Odeslat") == "Send"


# ── 5. Language-switch flow ────────────────────────────────────────────────────

class TestLanguageSwitchFlow:
    def test_set_language_route_cs_to_en(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "testuser"
            sess["language"] = "cs"
        resp = client.get("/set-language/en", follow_redirects=False)
        assert resp.status_code in (301, 302)
        with client.session_transaction() as sess:
            assert sess.get("language") == "en", (
                "/set-language/en did not update session['language']"
            )

    def test_set_language_route_rejects_invalid(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "testuser"
            sess["language"] = "cs"
        client.get("/set-language/xx", follow_redirects=False)
        with client.session_transaction() as sess:
            assert sess.get("language") == "cs", (
                "Invalid language code 'xx' must not overwrite session"
            )

    def test_settings_post_updates_session_language(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "testuser"
            sess["language"] = "cs"

        with patch("app.routes.bakalari_routes.fetch_row", return_value=None), \
             patch("app.routes.bakalari_routes._db_save_settings"), \
             patch("app.routes.bakalari_routes._db_get_settings", return_value={}):
            resp = client.post(
                "/api/settings",
                data=json.dumps({"language": "en"}),
                content_type="application/json",
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ok") is True, f"Expected ok=True, got: {data}"
        assert data.get("language_changed") is True, (
            "Response must include language_changed=True so the JS can reload"
        )
        with client.session_transaction() as sess:
            assert sess.get("language") == "en", (
                "POST /api/settings did not write 'en' into session['language']"
            )

    def test_settings_post_no_reload_when_language_same(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "testuser"
            sess["language"] = "en"

        with patch("app.routes.bakalari_routes.fetch_row", return_value=None), \
             patch("app.routes.bakalari_routes._db_save_settings"), \
             patch("app.routes.bakalari_routes._db_get_settings", return_value={}):
            resp = client.post(
                "/api/settings",
                data=json.dumps({"language": "en"}),
                content_type="application/json",
            )

        data = resp.get_json()
        assert data.get("language_changed") is False, (
            "language_changed must be False when language did not actually change "
            "(avoids pointless page reload)"
        )
