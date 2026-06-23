from __future__ import annotations

from dataclasses import dataclass, field

from safeplate.extraction2.interpret_structured import interpret_structured
from safeplate.extraction2 import interpret_llm
from safeplate.extraction2.verify import verify
from safeplate.extraction2.schema import Payload, PayloadKind
from safeplate.extraction2.pipeline import _dedupe_across_sources
from safeplate.menu_text import MenuItemRecord, _pdf_text_from_bytes
from safeplate.turbo.fetch import fetch_all
from safeplate.turbo.clean import clean_text, menu_links


@dataclass
class TurboResult:
    items: list[MenuItemRecord] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def _zero_metrics() -> dict:
    return {
        "fetches": 0,
        "llm_calls": 0,
        "structured_sources": 0,
        "llm_sources": 0,
        "items": 0,
    }


def _turbo_llm(text, payload, api_key, model, max_chunks):
    if not text.strip():
        return [], 0
    chunks = interpret_llm._chunks(text)[:max_chunks]
    parsed = [
        interpret_llm._cached_or_call(
            c, api_key=api_key, model=(model or interpret_llm.DEFAULT_MODEL)
        )
        for c in chunks
    ]
    merged: dict[str, MenuItemRecord] = {}
    for p in parsed:
        for rec in interpret_llm._records_from_parsed(p, payload):
            merged.setdefault(rec.item_name.lower(), rec)
    gp = Payload(
        url=payload.url,
        source_type=payload.source_type,
        kind=payload.kind,
        text=text,
    )
    kept, _dropped = verify(list(merged.values()), gp, require_grounding=True)
    return kept, len(chunks)


def extract_restaurant(
    *,
    name,
    website_url,
    address,
    categories,
    api_key,
    model,
    user_agent,
    max_sources=3,
    max_links=6,
    max_chunks=3,
) -> TurboResult:
    metrics = _zero_metrics()
    if not website_url.strip():
        return TurboResult([], metrics)

    home_results = fetch_all([website_url], user_agent=user_agent)
    metrics["fetches"] += len(home_results)
    homepage = home_results[0]
    home_html = homepage.content.decode("utf-8", errors="replace")

    links = menu_links(home_html, homepage.final_url, limit=max_links)
    link_results = fetch_all(links, user_agent=user_agent)
    metrics["fetches"] += len(link_results)

    candidates = [homepage] + [f for f in link_results if f.ok]
    candidates = candidates[:max_sources]

    all_items: list[MenuItemRecord] = []
    for cand in candidates:
        decoded_html = cand.content.decode("utf-8", errors="replace")
        is_pdf = (
            "pdf" in cand.content_type.lower()
            or cand.final_url.lower().endswith(".pdf")
        )
        payload = Payload(
            url=cand.final_url,
            source_type=("pdf" if is_pdf else "website_link"),
            kind=PayloadKind.TEXT,
            text=("" if is_pdf else decoded_html),
            content=(cand.content if is_pdf else None),
        )
        items = interpret_structured(payload)
        if items:
            metrics["structured_sources"] += 1
        elif api_key:
            text = (
                _pdf_text_from_bytes(cand.content)
                if is_pdf
                else clean_text(decoded_html)
            )
            kept, n_calls = _turbo_llm(text, payload, api_key, model, max_chunks)
            metrics["llm_calls"] += n_calls
            if kept:
                metrics["llm_sources"] += 1
                items = kept
        all_items.extend(items)

    items = _dedupe_across_sources(all_items)
    metrics["items"] = len(items)
    return TurboResult(items, metrics)
