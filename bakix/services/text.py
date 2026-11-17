"""Drobnosti pro práci s českým textem."""

import unicodedata


def fold(text: str) -> str:
    """Malá písmena bez diakritiky: "Žďár" → "zdar". Pro hledání a porovnávání."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


# Popisky známek, které neříkají, z čeho známka je.
_GENERIC = {
    "pisemka", "test", "zkouseni", "ustni zkouseni", "desetiminutovka", "domaci ukol",
    "aktivita", "aktivita v hodine", "ctvrtletni pisemka", "pololetni pisemka",
    "pisemna prace", "opakovani", "prace v hodine", "referat", "projekt",
}  # fmt: skip


def is_generic_caption(caption: str) -> bool:
    return fold(caption).strip(" .–-") in _GENERIC
