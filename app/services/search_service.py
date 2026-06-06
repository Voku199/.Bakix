import logging
import re

import requests

log = logging.getLogger(__name__)

_MAX_SNIPPET = 400   # chars per search result snippet
_FETCH_CHARS = 1200  # chars of page body to extract when fetching full content


def web_search(query: str, max_results: int = 4) -> list:
    """Search the web using DuckDuckGo. Returns list of {title, url, snippet}."""
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results))
        results = [
            {
                "title":   r.get("title", "").strip(),
                "url":     r.get("href", ""),
                "snippet": (r.get("body") or "")[:_MAX_SNIPPET],
            }
            for r in raw
            if r.get("href")
        ]
        print(f"[SEARCH] '{query}' → {len(results)} výsledků")
        return results
    except ImportError:
        log.warning("search_service: ddgs není nainstalovaný (pip install ddgs)")
        return _ddg_api_fallback(query, max_results)
    except Exception as exc:
        log.warning("web_search selhalo (%s), zkouším fallback", exc)
        return _ddg_api_fallback(query, max_results)


def _ddg_api_fallback(query: str, max_results: int) -> list:
    """Fallback — DuckDuckGo Instant Answer JSON API (bez extra balíčku)."""
    try:
        resp = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            timeout=8,
            headers={"User-Agent": "Bakix-AI/1.0"},
        )
        data = resp.json()
        results = []
        if data.get("Abstract"):
            results.append({
                "title":   data.get("Heading", query),
                "url":     data.get("AbstractURL", ""),
                "snippet": data.get("Abstract", "")[:_MAX_SNIPPET],
            })
        for t in (data.get("RelatedTopics") or []):
            if isinstance(t, dict) and t.get("Text") and t.get("FirstURL"):
                results.append({
                    "title":   t["Text"][:60],
                    "url":     t["FirstURL"],
                    "snippet": t["Text"][:_MAX_SNIPPET],
                })
        print(f"[SEARCH fallback] '{query}' → {len(results[:max_results])} výsledků")
        return results[:max_results]
    except Exception as exc:
        log.warning("_ddg_api_fallback selhalo: %s", exc)
        return []


def fetch_page_text(url: str, max_chars: int = _FETCH_CHARS) -> str:
    """Stáhne stránku a vrátí čistý text (bez HTML tagů)."""
    try:
        resp = requests.get(
            url, timeout=8,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Bakix-AI/1.0)"},
        )
        text = resp.text
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as exc:
        log.warning("fetch_page_text(%s) selhalo: %s", url, exc)
        return ""


def format_search_context(results: list, fetch_first: bool = False) -> str:
    """Zformátuje výsledky do kontextu pro AI."""
    if not results:
        return ""
    lines = ["=== VÝSLEDKY VYHLEDÁVÁNÍ ==="]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}")
        lines.append(f"    URL: {r['url']}")
        lines.append(f"    {r['snippet']}")
        if fetch_first and i == 1 and r["url"]:
            body = fetch_page_text(r["url"])
            if body:
                lines.append(f"    Obsah stránky: {body}")
        lines.append("")
    lines.append("=== KONEC VÝSLEDKŮ ===")
    return "\n".join(lines)


def format_sources_md(results: list) -> str:
    """Vrátí markdown sekci se zdroji pro konec zprávy."""
    if not results:
        return ""
    lines = ["---", "📚 **Zdroje:**"]
    for r in results:
        title = r.get("title") or r.get("url", "odkaz")
        url   = r.get("url", "")
        if url:
            lines.append(f"- [{title}]({url})")
        else:
            lines.append(f"- {title}")
    return "\n".join(lines)
