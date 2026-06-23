from __future__ import annotations

import os
from pathlib import Path

DEFAULT_LIMIT = 25
DEFAULT_RADIUS_METERS = 1500
DEFAULT_PROVIDER = "osm"

DEFAULT_USER_AGENT = "SafePlate student MVP/0.1"

DEFAULT_FETCH_CONCURRENCY = 8
# 12 matches the cold-search pipeline's natural pending-call count (the list extracts
# ~4 restaurants in parallel x ~3 menu sources each). Empirically a paid key handles
# >=32 concurrent full-size extraction calls with zero 429s + flat latency, so 12 is
# safe with headroom. A free-tier key may see 429s here -- it retries/backs off, so it
# degrades rather than breaks; lower SAFEPLATE_GEMINI_CONCURRENCY if you're free-tier.
DEFAULT_GEMINI_CONCURRENCY = 12
# Token-bucket request rate (calls/sec) for Gemini, in addition to the concurrency
# semaphore. A semaphore caps in-flight calls but NOT calls-per-window, so when a
# burst of calls all back off on a 429 and retry together they can re-trip the
# free-tier RPM wall (the documented cause of the old ~20% silent failures). Default
# 12/s ~= the concurrency cap at ~1s/call, so it never throttles a paid key in steady
# state; free-tier keys should lower SAFEPLATE_GEMINI_RPS (e.g. 0.25 for 15 RPM).
DEFAULT_GEMINI_RPS = 12.0

# Brave Search API rate governance. The paid plan caps at 50 queries/sec; we target
# 80% of that with a token bucket so burst/jitter (Brave counts per 1s window) can't
# trip 429s, and size the in-flight semaphore to saturate it -- Little's Law:
# concurrency ~= rps x per-query latency (~0.5s) -> ~20.
DEFAULT_BRAVE_RPS = 40.0
DEFAULT_BRAVE_CONCURRENCY = 20


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
    """Max parallel Gemini calls (global semaphore). Default 12; override with
    SAFEPLATE_GEMINI_CONCURRENCY (raise on a paid key, lower on free tier)."""
    return _positive_int_env("SAFEPLATE_GEMINI_CONCURRENCY", DEFAULT_GEMINI_CONCURRENCY)


def get_gemini_rps() -> float:
    """Token-bucket refill rate (calls/sec) for Gemini, paired with the concurrency
    semaphore so a retry burst can't re-trip the per-minute rate wall. Default 12/s;
    lower SAFEPLATE_GEMINI_RPS on a free-tier key (e.g. 0.25 for a 15 RPM quota)."""
    return _positive_float_env("SAFEPLATE_GEMINI_RPS", DEFAULT_GEMINI_RPS)


def get_brave_rps() -> float:
    """Token-bucket refill rate (queries/sec) for the Brave Search API. Default 40
    (80% of the 50/s paid cap, leaving headroom against 429s); override with
    SAFEPLATE_BRAVE_RPS."""
    return _positive_float_env("SAFEPLATE_BRAVE_RPS", DEFAULT_BRAVE_RPS)


def get_brave_concurrency() -> int:
    """Max parallel Brave calls (global semaphore). Default 20 (~rps x ~0.5s latency,
    by Little's Law); override with SAFEPLATE_BRAVE_CONCURRENCY."""
    return _positive_int_env("SAFEPLATE_BRAVE_CONCURRENCY", DEFAULT_BRAVE_CONCURRENCY)


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


DEFAULT_HTTP_MEMORY_CACHE_TTL = 3600


def get_http_memory_cache_ttl() -> int:
    """Seconds a page stays reusable in the *in-process* GET cache. Unlike the
    opt-in on-disk cache, this one is always on (it dedupes the discovery →
    extraction fetches within a single search). A long-running server would
    otherwise serve a page cached at startup forever, so entries older than this
    TTL are treated as misses. Default 1h keeps within-request reuse free while
    bounding cross-request staleness; <= 0 disables expiry (old behaviour).
    Override with SAFEPLATE_HTTP_MEMORY_CACHE_TTL."""
    raw_value = os.environ.get("SAFEPLATE_HTTP_MEMORY_CACHE_TTL")
    if raw_value is None or not raw_value.strip():
        return DEFAULT_HTTP_MEMORY_CACHE_TTL
    try:
        return int(raw_value.strip())
    except ValueError:
        return DEFAULT_HTTP_MEMORY_CACHE_TTL


def _positive_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if not raw_value:
        return default
    try:
        value = int(raw_value.strip())
    except ValueError:
        return default
    return value if value >= 1 else default


def _positive_float_env(name: str, default: float) -> float:
    raw_value = os.environ.get(name)
    if not raw_value:
        return default
    try:
        value = float(raw_value.strip())
    except ValueError:
        return default
    return value if value > 0 else default


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


# Scoring engine (the one user-facing choice). 'ai' = label-routing LLM scorer
# (default; falls back to the deterministic floor when there's no Gemini key/quota),
# 'rules' = deterministic only. Legacy 'v2'/'v3'/'ai_assisted'/'ai_full_menu' values
# still map in for back-compat. (Extraction is always the structured pipeline now.)
_SCORING_ALIASES = {
    "v2": "rules", "rules": "rules",
    "v3": "ai", "ai": "ai", "ai_assisted": "ai", "ai_full_menu": "ai",
    "ai_fullmenu": "ai", "full_menu": "ai",
}


def normalize_scoring_engine(value: str | None) -> str:
    """Map any scoring value to the canonical 'rules' | 'ai'. Unset/unknown -> 'ai'
    (the product default; it falls back to the deterministic scorer when there's no
    Gemini key/quota, so defaulting to it is always safe)."""
    return _SCORING_ALIASES.get(str(value or "").strip().lower(), "ai")


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
