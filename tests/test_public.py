"""Veřejné stránky, hlavičky a SEO."""

import json
import re

import pytest

PUBLIC = ["/", "/bezpecnost", "/soukromi", "/podminky"]


@pytest.mark.parametrize("path", PUBLIC)
def test_public_pages_are_indexable(client, path):
    response = client.get(path)
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "X-Robots-Tag" not in response.headers
    assert f'<link rel="canonical" href="https://bakix.test{path}">' in html
    assert '<meta name="description"' in html and 'property="og:image"' in html
    assert 'name="robots"' not in html
    assert len(re.findall(r"<h1\b", html)) == 1


def test_security_headers(client):
    headers = client.get("/").headers
    csp = headers["Content-Security-Policy"]
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "http" not in csp  # žádná cizí doména
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_landing_sets_no_cookie_and_loads_nothing_external(client):
    response = client.get("/")
    html = response.get_data(as_text=True)
    assert "Set-Cookie" not in response.headers
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    allowed = ("https://bakix.test", "https://knowix.cz")
    assert all(url.startswith(allowed) for url in external), external
    # Skripty, styly ani písma z cizí domény (canonical a og: jsou jen adresy, nic nenačítají).
    assert not re.search(r'<script[^>]+src="https?://', html)
    assert not re.search(r'<link rel="(?:stylesheet|preload|icon)"[^>]+href="https?://', html)


def test_json_ld_is_valid(client):
    html = client.get("/").get_data(as_text=True)
    block = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1)
    data = json.loads(block)
    types = {item["@type"] for item in data["@graph"]}
    assert types == {"WebSite", "WebApplication", "FAQPage"}


def test_robots_and_sitemap_use_public_url_not_host_header(client):
    robots = client.get("/robots.txt", headers={"Host": "zlo.example"}).get_data(as_text=True)
    assert "Sitemap: https://bakix.test/sitemap.xml" in robots
    assert "zlo.example" not in robots
    sitemap = client.get("/sitemap.xml", headers={"Host": "zlo.example"}).get_data(as_text=True)
    assert "<loc>https://bakix.test/</loc>" in sitemap
    assert "zlo.example" not in sitemap and "/prehled" not in sitemap


def test_manifest(client):
    response = client.get("/manifest.webmanifest")
    assert response.mimetype == "application/manifest+json"
    assert response.get_json()["short_name"] == "Bakix"


def test_unknown_page_is_404_without_details(client):
    response = client.get("/neexistuje")
    assert response.status_code == 404
    assert "Traceback" not in response.get_data(as_text=True)


def test_styleguide_hidden_outside_development(client):
    assert client.get("/styleguide").status_code == 404
