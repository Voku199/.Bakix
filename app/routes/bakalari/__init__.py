"""The bakalari blueprint, split by domain.

Importing the submodules registers their routes on the shared blueprint —
the import order at the bottom is what wires everything up.
"""

from flask import Blueprint

bakalari_bp = Blueprint("bakalari", __name__)

from app.routes.bakalari import (  # noqa: E402,F401 — route registration
    ai_chat, pages, school_api, settings, views,
)
