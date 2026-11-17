"""Design systém: šablony bez inline kódu (CSP), ikony a statické soubory."""

import re
from pathlib import Path

import pytest

from bakix.assets import STATIC_DIR

TEMPLATES = sorted((Path(__file__).parent.parent / "bakix" / "templates").rglob("*.html"))

# CSP nemá 'unsafe-inline': tohle by prohlížeč v produkci tiše zahodil.
INLINE_CODE = {
    "atribut style=": re.compile(r"\sstyle\s*=", re.I),
    "<style>": re.compile(r"<style\b", re.I),
    # JSON-LD jsou data, ne skript; CSP se na ně nevztahuje.
    "inline <script>": re.compile(r"<script\b(?![^>]*\bsrc=)(?![^>]*application/ld\+json)", re.I),
    "obsluha on…=": re.compile(r"\son[a-z]+\s*=", re.I),
    "javascript: URL": re.compile(r"javascript:", re.I),
}


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_templates_have_no_inline_code(template):
    source = template.read_text(encoding="utf-8")
    found = [name for name, pattern in INLINE_CODE.items() if pattern.search(source)]
    assert not found, f"{template.name}: {', '.join(found)} (CSP to zablokuje)"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_post_forms_have_csrf_token(template):
    source = template.read_text(encoding="utf-8")
    for form in re.findall(r'<form\b[^>]*method="post".*?</form>', source, re.S | re.I):
        assert "csrf_token()" in form, f"{template.name}: POST formulář bez CSRF tokenu"


def test_used_icons_exist_in_sprite():
    sprite = (STATIC_DIR / "img" / "icons.svg").read_text(encoding="utf-8")
    available = set(re.findall(r'<symbol id="([^"]+)"', sprite))
    used = set()
    for template in TEMPLATES:
        source = template.read_text(encoding="utf-8")
        used |= set(re.findall(r'\bicon\(\s*"([a-z0-9-]+)"', source))
        used |= set(re.findall(r'"([a-z0-9-]+)", \("', source))  # ikony v seznamu záložek
    assert len(used) > 10
    assert used <= available, f"Chybí ve spritu: {sorted(used - available)}"


def test_css_references_existing_fonts_and_nothing_external():
    css = (STATIC_DIR / "css" / "app.css").read_text(encoding="utf-8")
    fonts = re.findall(r'url\("\.\./fonts/([^")]+)"\)', css)
    assert len(fonts) == 6
    for font in fonts:
        assert (STATIC_DIR / "fonts" / font).is_file(), font
    assert "http://" not in css and "https://" not in css and "@import" not in css


def test_assets_are_versioned_and_cached(client):
    html = client.get("/").get_data(as_text=True)
    match = re.search(r'href="(/static/css/app\.css\?v=[0-9a-f]{10})"', html)
    assert match, "Stylesheet nemá verzi v URL"
    response = client.get(match.group(1))
    assert "immutable" in response.headers["Cache-Control"]
    response.close()


def test_no_audio_or_tracking_in_static():
    files = [p.suffix for p in STATIC_DIR.rglob("*") if p.is_file()]
    assert not {".mp3", ".wav", ".map"} & set(files)


def test_default_palette_is_in_html(client):
    html = client.get("/").get_data(as_text=True)
    assert '<html lang="cs" data-palette="voku" data-shape="kulaty">' in html


class TestPalette:
    @pytest.fixture
    def app_overrides(self):
        return {"PALETTE": "inkoust"}

    def test_palette_comes_from_config(self, client):
        html = client.get("/").get_data(as_text=True)
        assert 'data-palette="inkoust"' in html and 'content="#eef0f2"' in html

    def test_preview_cookie_is_ignored_outside_development(self, client):
        client.set_cookie("bakix_paleta", "grafit")
        assert 'data-palette="inkoust"' in client.get("/").get_data(as_text=True)


def test_every_palette_has_light_and_dark_tokens():
    from bakix.design import DEFAULT, PALETTES

    css = (STATIC_DIR / "css" / "app.css").read_text(encoding="utf-8")
    light, dark = css.split("@media (prefers-color-scheme: dark)", 1)
    for key, palette in PALETTES.items():
        if key == DEFAULT:
            continue
        selector = f':root[data-palette="{key}"]'
        assert selector in light and selector in dark, key
        assert f"--bg: {palette.light_bg};" in light and f"--bg: {palette.dark_bg};" in dark, key


def test_unknown_palette_is_rejected(monkeypatch):
    from bakix.config import DevelopmentConfig

    monkeypatch.setattr(DevelopmentConfig, "PALETTE", "duha")
    with pytest.raises(RuntimeError, match="BAKIX_PALETTE"):
        DevelopmentConfig.validate()


def test_every_shape_set_is_defined_in_css():
    from bakix.design import DEFAULT_SHAPE, SHAPES

    css = (STATIC_DIR / "css" / "app.css").read_text(encoding="utf-8")
    for key in SHAPES:
        if key != DEFAULT_SHAPE:
            assert f':root[data-shape="{key}"]' in css, key


def test_unknown_shape_is_rejected(monkeypatch):
    from bakix.config import DevelopmentConfig

    monkeypatch.setattr(DevelopmentConfig, "SHAPE", "trojuhelnik")
    with pytest.raises(RuntimeError, match="BAKIX_SHAPE"):
        DevelopmentConfig.validate()


class TestShape:
    @pytest.fixture
    def app_overrides(self):
        return {"SHAPE": "hranaty"}

    def test_shape_comes_from_config(self, client):
        assert 'data-shape="hranaty"' in client.get("/").get_data(as_text=True)
