"""Engine-independent LLM judge for the extraction scorecard.

The regex proxy (`compare_engines.looks_like_junk`) can't actually tell a dish
from a book title or a legal sentence -- it just pattern-matches. This judge asks
Gemini, per item, "is this a real orderable restaurant menu item?" so precision
becomes a trustworthy number instead of a heuristic guess.

Fairness + cost: each DISTINCT item string is judged exactly once (cached on disk
by normalized name + model) and the verdict is reused across all engines, so the
judge never sees which engine produced an item and reruns are free.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from safeplate.concurrency import map_concurrent
from safeplate.config import get_cache_dir
from safeplate.extraction2.interpret_llm import _call_with_retry
from safeplate.gemini_menu import GeminiMenuError

JUDGE_SYSTEM = (
    "You are auditing a restaurant menu extractor. For each numbered line, decide "
    "whether it names a REAL, orderable restaurant menu item -- a food dish, drink, "
    "side, dessert, combo, sauce, or add-on/modifier. Set is_dish=true for those, "
    "even when no price is shown. Set is_dish=false for anything that is NOT an "
    "orderable menu item, including: navigation/buttons/UI text, addresses, phone "
    "numbers, opening hours, bare prices or numbers, legal/corporate/policy "
    "sentences, marketing prose, loyalty-program text, book or retail product "
    "names, section headers, allergen-disclaimer sentences, and truncated "
    "fragments. Judge only the text given. Return exactly one verdict per id."
)

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "is_dish": {"type": "boolean"},
                },
                "required": ["id", "is_dish"],
            },
        }
    },
    "required": ["verdicts"],
}


def normalize(name: str) -> str:
    return " ".join((name or "").split()).lower()


def _cache_path(name: str, model: str):
    digest = hashlib.sha1(f"{model}:{normalize(name)}".encode("utf-8")).hexdigest()
    return get_cache_dir() / "eval_judge" / f"{digest}.json"


def _load(name: str, model: str) -> bool | None:
    try:
        return bool(json.loads(_cache_path(name, model).read_text(encoding="utf-8"))["is_dish"])
    except (OSError, ValueError, KeyError):
        return None


def _save(name: str, model: str, is_dish: bool) -> None:
    path = _cache_path(name, model)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"name": name, "is_dish": is_dish}), encoding="utf-8")
    except OSError:
        pass


def judge_items(
    items: list[dict],
    *,
    api_key: str,
    model: str,
    batch_size: int = 40,
    workers: int = 4,
) -> dict[str, bool]:
    """items: dicts with name/description/price. Returns {normalized_name: is_dish}."""
    uniq: dict[str, dict] = {}
    for it in items:
        uniq.setdefault(normalize(it.get("name", "")), it)
    uniq.pop("", None)

    verdicts: dict[str, bool] = {}
    todo: list[dict] = []
    for norm_name, it in uniq.items():
        cached = _load(it.get("name", ""), model)
        if cached is None:
            todo.append(it)
        else:
            verdicts[norm_name] = cached

    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]

    def run(batch: list[dict]):
        return _judge_batch(batch, api_key=api_key, model=model)

    for results in map_concurrent(run, batches, max_workers=workers):
        for it, is_dish in results:
            verdicts[normalize(it.get("name", ""))] = is_dish
            _save(it.get("name", ""), model, is_dish)
    return verdicts


def _judge_batch(batch: list[dict], *, api_key: str, model: str):
    lines = []
    for i, it in enumerate(batch):
        ctx = str(it.get("name", ""))
        if it.get("description"):
            ctx += f"  [desc: {str(it['description'])[:80]}]"
        if it.get("price"):
            ctx += f"  [price: {it['price']}]"
        lines.append(f"{i}: {ctx}")
    request = {
        "system_instruction": {"parts": [{"text": JUDGE_SYSTEM}]},
        "contents": [{"parts": [{"text": "Classify each line:\n" + "\n".join(lines)}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": JUDGE_SCHEMA,
        },
    }
    try:
        parsed = _call_with_retry(request, api_key=api_key, model=model)
    except GeminiMenuError:
        # Fail-open: a judge outage shouldn't fabricate a precision penalty.
        return [(it, True) for it in batch]
    by_id = {
        v.get("id"): bool(v.get("is_dish"))
        for v in parsed.get("verdicts", [])
        if isinstance(v, dict)
    }
    # The judge RAN: an item it omitted is NOT a confirmed dish. Default missing ids to
    # False so an incomplete judge response can't silently inflate precision. (A full
    # judge OUTAGE is handled above with the deliberate fail-open.)
    return [(it, by_id.get(i, False)) for i, it in enumerate(batch)]
