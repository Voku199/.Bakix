"""Flask extensions shared across blueprints.

Defined here (without an app) so route modules can import the instances at
import time and decorate views, while create_app() wires them up via init_app().
"""

import os

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect

# No default limits — only explicitly decorated endpoints are throttled, so
# static assets and the dashboard polling stay unaffected.
#
# In-memory storage is fine for a single waitress process (threads share the
# counters), but it is per-process: run several workers/instances and each keeps
# its own counts, so the brute-force limits weaken. Point RATELIMIT_STORAGE_URI
# at a shared store (e.g. redis://...) in any multi-process deployment.
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
    default_limits=[],
)

csrf = CSRFProtect()
