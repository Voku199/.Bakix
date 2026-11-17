"""Bezpečnostní hlavičky. CSP bez unsafe-inline: JS a CSS patří do souborů ve static/.

Aplikace nenačítá nic z cizích domén (žádné CDN, žádné Google Fonts, žádná analytika),
takže stačí 'self' a prohlížeč cokoli jiného zahodí.
"""

from flask import Flask, Response, request

_DIRECTIVES = {
    "default-src": "'self'",
    "script-src": "'self'",
    "style-src": "'self'",
    "img-src": "'self' data:",
    "font-src": "'self'",
    "connect-src": "'self'",
    "manifest-src": "'self'",
    "object-src": "'none'",
    "base-uri": "'self'",
    "form-action": "'self'",
    "frame-ancestors": "'none'",
}

CSP = "; ".join(f"{name} {value}" for name, value in _DIRECTIVES.items())

# Endpointy, které smí vidět vyhledávač. Všechno ostatní dostane noindex a no-store.
PUBLIC_ENDPOINTS = frozenset(
    {
        "public.index",
        "public.security",
        "public.privacy_policy",
        "public.terms",
        "public.robots",
        "public.sitemap",
        "public.manifest",
        "static",
    }
)


def register_security_headers(app: Flask) -> None:
    @app.after_request
    def set_security_headers(response: Response) -> Response:
        headers = response.headers
        headers.setdefault("Content-Security-Policy", CSP)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
        )
        if app.config.get("SESSION_COOKIE_SECURE"):
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

        if request.endpoint not in PUBLIC_ENDPOINTS:
            # Známky a zprávy nemají co dělat v cache prohlížeče, proxy ani ve vyhledávači.
            headers["Cache-Control"] = "no-store"
            headers["X-Robots-Tag"] = "noindex, nofollow"
        return response
