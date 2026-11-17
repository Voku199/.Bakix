"""Statické soubory s verzí v URL.

V šablonách `{{ asset('css/app.css') }}` vrátí `/static/css/app.css?v=<hash obsahu>`. Po změně
souboru se změní URL, takže si ho prohlížeč může cachovat napořád a po nasazení stejně dostane
novou verzi. CSS je psané ručně, žádný build krok není potřeba.
"""

import hashlib
from functools import cache
from pathlib import Path

from flask import Flask, Response, request, url_for

STATIC_DIR = Path(__file__).parent / "static"
ONE_YEAR = 365 * 24 * 60 * 60


@cache
def _file_hash(path: Path, mtime_ns: int) -> str:
    """Hash obsahu. mtime je v klíči cache, takže se změna souboru ve vývoji projeví hned."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def asset_url(filename: str) -> str:
    path = STATIC_DIR / filename
    try:
        version = _file_hash(path, path.stat().st_mtime_ns)
    except FileNotFoundError:
        return url_for("static", filename=filename)
    return url_for("static", filename=filename, v=version)


def register_assets(app: Flask) -> None:
    app.add_template_global(asset_url, name="asset")

    @app.after_request
    def cache_versioned_static(response: Response) -> Response:
        if request.endpoint == "static" and "v" in request.args and response.status_code == 200:
            response.cache_control.public = True
            response.cache_control.max_age = ONE_YEAR
            response.cache_control.immutable = True
        return response
