"""SEO: robots.txt, sitemap.xml a strukturovaná data (JSON-LD) pro úvodní stránku.

Adresy se skládají z PUBLIC_URL, nikdy z hlavičky Host (tu může poslat kdokoli jakoukoli).
`lastmod` je skutečné datum změny šablony, ne dnešek: vyhledávač, kterému sitemap pokaždé
tvrdí „změněno dnes“, jí přestane věřit.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

from flask import current_app, url_for

WEEK = timedelta(days=7)
TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

# (endpoint, šablona, priorita)
PAGES = (
    ("public.index", "public/welcome.html", "1.0"),
    ("public.security", "public/security.html", "0.7"),
    ("public.privacy_policy", "public/privacy.html", "0.3"),
    ("public.terms", "public/terms.html", "0.3"),
)

FAQ = (
    (
        "Je Bakix oficiální aplikace Bakalářů?",
        "Ne. Bakix je nezávislý projekt, který s Bakaláři není nijak spojený. Jen přehledněji "
        "zobrazuje data, která ti škola v Bakalářích zpřístupňuje.",
    ),
    (
        "Ukládá Bakix moje heslo?",
        "Ne. Heslo použije jednou při přihlášení a zahodí ho. Dál si pamatuje jen zašifrovaný "
        "přístupový token, který můžeš kdykoli zneplatnit odhlášením nebo smazáním účtu.",
    ),
    (
        "Ukládá Bakix moje známky?",
        "Ne. Známky, rozvrh, úkoly ani zprávy se neukládají do databáze. Načtou se ze školy "
        "ve chvíli, kdy se díváš, a za pár minut jsou z paměti pryč.",
    ),
    (
        "Můžu Bakix používat jako rodič?",
        "Ano. Přihlásíš se stejnými údaji jako do rodičovského účtu v Bakalářích a uvidíš "
        "totéž co tam, jen přehledněji.",
    ),
    (
        "Jak funguje pomoc s učením?",
        "Když se objeví špatná známka, Bakix z předmětu a probíraného tématu sestaví hotové "
        "zadání. To si vložíš do AI, kterou používáš, a ta ti látku vysvětlí krok za krokem.",
    ),
    (
        "Kolik to stojí?",
        "Bakix je zdarma a bez reklam.",
    ),
)


def public_url(endpoint: str, **values) -> str:
    return current_app.config["PUBLIC_URL"].rstrip("/") + url_for(endpoint, **values)


def robots_txt() -> str:
    lines = [
        "User-agent: *",
        "Allow: /$",
        "Allow: /bezpecnost",
        "Allow: /soukromi",
        "Allow: /podminky",
        "Allow: /static/",
        "Disallow: /",
        "",
        f"Sitemap: {public_url('public.sitemap')}",
        "",
    ]
    return "\n".join(lines)


def _lastmod(template: str) -> str:
    try:
        modified = (TEMPLATES / template).stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(modified, UTC).date().isoformat()


def sitemap_xml() -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for endpoint, template, priority in PAGES:
        lines.append("  <url>")
        lines.append(f"    <loc>{escape(public_url(endpoint))}</loc>")
        lastmod = _lastmod(template)
        if lastmod:
            lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append(f"    <priority>{priority}</priority>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def landing_json_ld() -> str:
    """Strukturovaná data úvodní stránky. `<` se escapuje, aby text nemohl ukončit <script>."""
    home = public_url("public.index")
    graph = [
        {
            "@type": "WebSite",
            "@id": home + "#web",
            "url": home,
            "name": "Bakix",
            "inLanguage": "cs",
            "description": "Známky, rozvrh, úkoly a zprávy z Bakalářů na jeden pohled.",
        },
        {
            "@type": "WebApplication",
            "@id": home + "#app",
            "name": "Bakix",
            "url": home,
            "applicationCategory": "EducationalApplication",
            "operatingSystem": "Web",
            "inLanguage": "cs",
            "isAccessibleForFree": True,
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "CZK"},
            "audience": {"@type": "EducationalAudience", "educationalRole": ["student", "parent"]},
        },
        {
            "@type": "FAQPage",
            "@id": home + "#faq",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": question,
                    "acceptedAnswer": {"@type": "Answer", "text": answer},
                }
                for question, answer in FAQ
            ],
        },
    ]
    data = {"@context": "https://schema.org", "@graph": graph}
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
