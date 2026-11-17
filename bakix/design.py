"""Palety. Vzhled (písma, linky, komponenty) je jeden, paleta mění jen barvy.

Vybírá se v .env: BAKIX_PALETTE=voku | inkoust | tabule | grafit | svestka.
Stejně se vybírají tvary: BAKIX_SHAPE=kulaty | mix | hranaty.
Barvy samotné jsou v static/css/app.css (blok :root[data-palette="…"]); tady je jen to,
co potřebuje Python: popis pro /styleguide a barvy pro <meta name="theme-color"> a manifest.
Nová paleta = nový blok v CSS (světlý i tmavý) + řádek tady.
"""

from dataclasses import dataclass

from flask import current_app, request


@dataclass(frozen=True)
class Palette:
    key: str
    name: str
    note: str
    light_bg: str
    dark_bg: str
    accent: str


PALETTES = {
    p.key: p
    for p in (
        Palette("voku", "Voku", "Teplý papír, inkoust a jíl. Barvy z vokudev.cz.",
                "#f0eee6", "#191815", "#a8482a"),
        Palette("inkoust", "Inkoust", "Chladnější papír a modrý inkoust. Nejvíc „školní“.",
                "#eef0f2", "#14171b", "#274c8a"),
        Palette("tabule", "Tabule", "Tlumená zelená školní tabule.",
                "#eef0e9", "#151916", "#2f6044"),
        Palette("grafit", "Grafit", "Černobílá. Jediná barva na stránce jsou známky.",
                "#f3f3f1", "#121212", "#111111"),
        Palette("svestka", "Švestka", "Vínová. Nejméně obvyklá, pořád klidná.",
                "#f1eef0", "#181517", "#7a2e52"),
    )
}  # fmt: skip
DEFAULT = "voku"
PREVIEW_COOKIE = "bakix_paleta"

# Tvary: klíč → (název, popis). Poloměry jsou v app.css (:root[data-shape="…"]).
SHAPES = {
    "kulaty": ("Kulatý", "Zaoblené panely i ovládání, známky v kroužku."),
    "mix": ("Mix", "Rám stránky hranatý, tlačítka, štítky, známky a tipy kulaté."),
    "hranaty": ("Hranatý", "Ostré rohy všude, jen tlačítka a pole mají malé zaoblení."),
}
DEFAULT_SHAPE = "kulaty"
SHAPE_COOKIE = "bakix_tvar"


def current() -> Palette:
    """Paleta z konfigurace. Ve vývoji ji jde přepnout na /styleguide (cookie), ať se dají
    palety porovnat bez restartu."""
    key = current_app.config.get("PALETTE", DEFAULT)
    if current_app.config.get("STYLEGUIDE_PUBLIC"):
        key = request.cookies.get(PREVIEW_COOKIE, key)
    return PALETTES.get(key, PALETTES[DEFAULT])


def current_shape() -> str:
    """Sada tvarů z konfigurace (BAKIX_SHAPE). Ve vývoji ji jde přepnout na /styleguide."""
    key = current_app.config.get("SHAPE", DEFAULT_SHAPE)
    if current_app.config.get("STYLEGUIDE_PUBLIC"):
        key = request.cookies.get(SHAPE_COOKIE, key)
    return key if key in SHAPES else DEFAULT_SHAPE
