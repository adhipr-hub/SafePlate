"""Turbo: an alternate, efficiency-first extraction core (benchmarked against the
current extraction2 pipeline).

Keeps the proven parts of the current pipeline -- structured-first extraction
(`interpret_structured`), the grounding guardrail (`verify`), and the Layer-5
scorer -- but swaps the slow machinery for faster equivalents found via research:

- async ``httpx`` fetching with tight connect/read timeouts + a download size cap
  (vs thread-per-restaurant ``requests`` with a 30s timeout and no size cap);
- ``selectolax`` (Lexbor) for HTML link discovery (5-30x faster than BeautifulSoup);
- ``trafilatura`` main-content extraction to shrink the text sent to the LLM;
- fewer sources and a capped chunk count to cut LLM calls.

All three deps are optional; importing this module without them raises a clear
error, and the live app never imports it.
"""

from safeplate.turbo.extract import TurboResult, extract_restaurant

__all__ = ["TurboResult", "extract_restaurant"]
