"""Token + dollar cost estimate for v2's LLM interpreter: current (12k cap) vs
recall-tuned (full-text chunking).

Input token counts are CALIBRATED against the Gemini API's own usageMetadata
(a few real calls), then extrapolated by the exact input sizes the interpreter
sends over all 168 snapshot sources. Output tokens come from the cached responses.
Price is the only assumption (stated below) and the totals scale linearly with it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from safeplate.config import get_gemini_api_key, get_gemini_model
from safeplate.gemini_menu import _post_gemini_generate_content
from safeplate.extraction2.acquire import payload_from_html, payload_from_pdf_text
from safeplate.extraction2.interpret_llm import (
    TEXT_SYSTEM_INSTRUCTION,
    _chunks,
    _readable_text,
)
from safeplate.menu_fetch_llm import URL_MENU_SCHEMA

# --- price assumption (Gemini Flash-Lite class). Adjust to your real rate; the
# totals scale linearly. As of 2025, 2.5-flash-lite was ~$0.10/$0.40 per 1M. ---
PRICE_IN_PER_TOK = 0.10 / 1_000_000
PRICE_OUT_PER_TOK = 0.40 / 1_000_000

SNAP_DIR = ROOT / "data" / "bench_snapshots"
MANIFEST = SNAP_DIR / "manifest.json"
OVERHEAD_CHARS = len(TEXT_SYSTEM_INSTRUCTION) + len("Menu page text:\n\n")
CURRENT_CAP = 12000


def _request(chunk: str) -> dict:
    return {
        "system_instruction": {"parts": [{"text": TEXT_SYSTEM_INSTRUCTION}]},
        "contents": [{"parts": [{"text": "Menu page text:\n\n" + chunk}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": URL_MENU_SCHEMA,
        },
    }


def calibrate(samples: list[str], api_key: str, model: str):
    """Least-squares fit prompt_tokens = a + b*input_chars from real calls, so the
    fixed system+schema overhead (a) and the per-char rate (b) are both measured.
    Returns (a, b, avg_output_tokens_per_call)."""
    xs, ys, outs = [], [], []
    for chunk in samples:
        resp = _post_gemini_generate_content(payload=_request(chunk), api_key=api_key, model=model)
        um = resp.get("usageMetadata", {})
        xs.append(len(chunk) + OVERHEAD_CHARS)
        ys.append(um.get("promptTokenCount", 0))
        outs.append(um.get("candidatesTokenCount", 0))
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs) or 1.0
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    a = my - b * mx
    return a, b, sum(outs) / n


def main() -> None:
    api_key = get_gemini_api_key()
    model = get_gemini_model()
    if not api_key:
        print("GEMINI_API_KEY not set")
        return
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    # Per-source input sizing for both configs.
    cur_calls = cur_in_chars = 0
    tun_calls = tun_in_chars = 0
    sample_chunks: list[str] = []
    for e in manifest:
        is_pdf = e["file"].endswith(".pdf.txt")
        text = (SNAP_DIR / e["file"]).read_text(encoding="utf-8")
        p = payload_from_pdf_text(e["url"], text) if is_pdf else payload_from_html(e["url"], text)
        clean = _readable_text(p)
        if not clean.strip():
            continue
        # current: single call, hard 12k cap
        cur_calls += 1
        cur_in_chars += min(len(clean), CURRENT_CAP) + OVERHEAD_CHARS
        # tuned: overlapping chunks of the full text
        chs = _chunks(clean)
        tun_calls += len(chs)
        tun_in_chars += sum(len(c) + OVERHEAD_CHARS for c in chs)
        if len(sample_chunks) < 6:
            sample_chunks.append(chs[0])

    # spread calibration samples across sizes
    sample_chunks = sorted(set(sample_chunks), key=len)
    a, b, avg_out = calibrate(sample_chunks, api_key, model)
    print(f"Calibration ({len(sample_chunks)} real calls, model={model}): "
          f"prompt_tokens ~= {a:.0f} + {b:.4f}*chars ; avg output ~= {avg_out:.0f} tok/call\n")

    def cost(calls, in_chars):
        # prompt_tokens = a (fixed system+schema per call) + b * input_chars
        in_tok = a * calls + b * in_chars
        out_tok = avg_out * calls
        dollars = in_tok * PRICE_IN_PER_TOK + out_tok * PRICE_OUT_PER_TOK
        return in_tok, out_tok, dollars

    n_rest = len({e["restaurant"] for e in manifest})
    print(f"{'CONFIG':10}{'calls':>8}{'in_tok':>12}{'out_tok':>11}{'$ / run':>11}"
          f"{'$ / source':>12}{'$ / restaurant':>16}")
    print("-" * 80)
    for label, calls, in_chars in (
        ("current", cur_calls, cur_in_chars),
        ("tuned", tun_calls, tun_in_chars),
    ):
        in_tok, out_tok, dollars = cost(calls, in_chars)
        per_src = dollars / cur_calls  # cur_calls == #sources
        per_rest = dollars / n_rest
        print(f"{label:10}{calls:>8}{in_tok:>12,.0f}{out_tok:>11,.0f}{dollars:>11.4f}"
              f"{per_src:>12.5f}{per_rest:>16.4f}")
    print("-" * 80)
    print(f"168 sources across {n_rest} restaurants (~{cur_calls / n_rest:.1f} sources/restaurant).")
    print(f"Price assumed: ${PRICE_IN_PER_TOK*1e6:.2f}/1M input, "
          f"${PRICE_OUT_PER_TOK*1e6:.2f}/1M output (Flash-Lite class).")
    print("Production note: HYBRID calls the LLM only when structured parse is empty,")
    print("and live discovery yields fewer sources/restaurant than this benchmark,")
    print("so real per-search cost is below the per-restaurant figure here.")


if __name__ == "__main__":
    main()
