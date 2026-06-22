"""Back-compat facade. The app used to be one big module; it's now split into:
  - common         -- leaf helpers (env/coerce, request->profile, payload shapers)
  - menu_service   -- structured extraction + Layer-#5 scoring, menu/drawer responses
  - search_service -- nearby lookup + ranked result cards
  - api_server     -- the HTTP server, routing, auth, rate-limit

This module re-exports the public surface so existing imports (the entrypoint,
tests, scripts) keep working unchanged. New code should import from the modules above.
"""

from safeplate.api_server import (  # noqa: F401
    create_app_handler,
    run_server,
    server_namespace,
)
from safeplate.menu_service import run_menu_extraction  # noqa: F401
from safeplate.search_service import run_restaurant_search  # noqa: F401

__all__ = [
    "create_app_handler",
    "run_server",
    "server_namespace",
    "run_menu_extraction",
    "run_restaurant_search",
]
