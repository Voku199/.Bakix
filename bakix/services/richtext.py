"""Odpověď AI jako bezpečné HTML.

Text se nejdřív celý escapuje a teprve potom se z něj skládá pár známých značek (nadpis,
odstavec, seznam, tučně, kód). HTML od modelu se nikdy nevykresluje, takže ani zmanipulovaná
odpověď (prompt injection přes název tématu) nedostane do stránky skript.
"""

import re

from markupsafe import Markup, escape

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")
_HEADING = re.compile(r"^#{1,4}\s+(.*)$")
_BULLET = re.compile(r"^[-*•]\s+(.*)$")
_NUMBER = re.compile(r"^\d{1,2}[.)]\s+(.*)$")


def _inline(text: str) -> str:
    safe = str(escape(text))
    safe = _BOLD.sub(r"<strong>\1</strong>", safe)
    return _CODE.sub(r"<code>\1</code>", safe)


def render(text: str) -> Markup:
    out: list[str] = []
    paragraph: list[str] = []
    list_tag = ""

    def flush_paragraph() -> None:
        if paragraph:
            out.append("<p>" + "<br>".join(paragraph) + "</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = ""

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush_paragraph()
            close_list()
            continue
        heading = _HEADING.match(line)
        bullet = _BULLET.match(line)
        number = _NUMBER.match(line)
        if heading:
            flush_paragraph()
            close_list()
            out.append(f"<h3>{_inline(heading.group(1))}</h3>")
        elif bullet or number:
            flush_paragraph()
            tag = "ul" if bullet else "ol"
            if list_tag != tag:
                close_list()
                out.append(f"<{tag}>")
                list_tag = tag
            out.append(f"<li>{_inline((bullet or number).group(1))}</li>")
        else:
            close_list()
            paragraph.append(_inline(line))
    flush_paragraph()
    close_list()
    return Markup("".join(out))  # noqa: S704  (vše escapováno v _inline)
