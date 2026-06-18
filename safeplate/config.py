from __future__ import annotations

import os
from pathlib import Path

DEFAULT_LIMIT = 25
DEFAULT_RADIUS_METERS = 1500
DEFAULT_PROVIDER = "osm"

DEFAULT_USER_AGENT = "SafePlate student MVP/0.1"

DEFAULT_FETCH_CONCURRENCY = 8
DEFAULT_GEMINI_CONCURRENCY = 4


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from a repo-root .env into os.environ.

    The project ships a .env.example but had no loader, so config only saw real
    OS environment variables. This makes a local .env "just work" for the app,
    scripts, and eval -- dependency-free, and it never overrides a variable that
    is already set (an explicit export still wins).
    """
    env_path = Path(__file__).resolve().parents[1] / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def get_user_agent() -> str:
    return os.environ.get("SAFEPLATE_USER_AGENT", DEFAULT_USER_AGENT)


def get_cache_dir() -> Path:
    """On-disk cache root (robots.txt, etc.). Override with SAFEPLATE_CACHE_DIR."""
    override = os.environ.get("SAFEPLATE_CACHE_DIR")
    if override and override.strip():
        return Path(override.strip())
    return Path(__file__).resolve().parents[1] / "data" / ".cache"


def get_fetch_concurrency() -> int:
    """Worker count for parallel page fetching across restaurants/sources."""
    return _positive_int_env("SAFEPLATE_FETCH_CONCURRENCY", DEFAULT_FETCH_CONCURRENCY)


def get_gemini_concurrency() -> int:
    """Worker count for parallel Gemini calls. Keep modest for rate limits."""
    return _positive_int_env("SAFEPLATE_GEMINI_CONCURRENCY", DEFAULT_GEMINI_CONCURRENCY)


def get_http_cache_ttl() -> int:
    """Seconds to reuse a fetched page from a persistent on-disk cache across
    separate runs (e.g. the find-menu-sources → extract-text CLI flow). 0
    (default) disables it, so it never silently serves stale pages — set
    SAFEPLATE_HTTP_CACHE_TTL to opt in."""
    raw_value = os.environ.get("SAFEPLATE_HTTP_CACHE_TTL")
    if not raw_value:
        return 0
    try:
        value = int(raw_value.strip())
    except ValueError:
        return 0
    return max(0, value)


def _positive_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if not raw_value:
        return default
    try:
        value = int(raw_value.strip())
    except ValueError:
        return default
    return value if value >= 1 else default


def get_geoapify_api_key() -> str | None:
    value = os.environ.get("GEOAPIFY_API_KEY")
    if value and value.strip():
        return value.strip()
    return None


def get_google_places_api_key() -> str | None:
    value = os.environ.get("GOOGLE_PLACES_API_KEY")
    if value and value.strip():
        return value.strip()
    return None


def get_brave_search_api_key() -> str | None:
    value = os.environ.get("BRAVE_SEARCH_API_KEY")
    if value and value.strip():
        return value.strip()
    return None


def get_gemini_api_key() -> str | None:
    value = os.environ.get("GEMINI_API_KEY")
    if value and value.strip():
        return value.strip()
    return None


def get_engine() -> str:
    """Menu-extraction engine for the app: 'v1' (legacy default) or 'v2'
    (clean-architecture extraction2 + Layer #5 scoring). Override per-request with
    an 'engine' field or globally with SAFEPLATE_ENGINE."""
    value = os.environ.get("SAFEPLATE_ENGINE", "v2").strip().lower()
    return value if value in ("v1", "v2") else "v2"


def get_gemini_model() -> str:
    return os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()


def get_gemini_fallback_models() -> list[str]:
    raw_value = os.environ.get(
        "GEMINI_FALLBACK_MODELS",
        "gemini-flash-lite-latest,gemini-2.5-flash-lite,gemini-2.0-flash-lite",
    )
    models: list[str] = []
    for model in raw_value.split(","):
        cleaned = model.strip()
        if cleaned and cleaned not in models:
            models.append(cleaned)
    return models
