"""Flask extensions shared across blueprints.

Defined here (without an app) so route modules can import the instances at
import time and decorate views, while create_app() wires them up via init_app().
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect

# No default limits — only explicitly decorated endpoints are throttled, so
# static assets and the dashboard polling stay unaffected. In-memory storage is
# fine for a single waitress process (threads share the counters).
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri="memory://",
    default_limits=[],
)

csrf = CSRFProtect()
