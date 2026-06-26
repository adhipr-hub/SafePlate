"""Shared embedded-JSON harvesting + traversal.

Both `embedded_json` (name+price menu items) and `extraction2.embedded_allergens`
(name+allergen dishes) used to carry their own copy of: the script/state-blob
harvester and the recursive object walker. That logic lives here once now; each
caller supplies its own `item_fn` (object -> record) and `key_fn` (dedupe key).
Depends only on `soup` (no menu_text), so there is no import cycle.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from safeplate.soup import make_soup

# Common dish-name keys across menu/ordering JSON shapes.
NAME_KEYS = {"name", "title", "itemname", "item_name", "displayname", "label"}

# Framework hydration globals that hold client-side state as inline-assigned JSON.
_STATE_VARS = (
    "__NEXT_DATA__", "__NUXT__", "__APOLLO_STATE__", "__INITIAL_STATE__",
    "__PRELOADED_STATE__", "__REDUX_STATE__",
)

_MAX_NODES = 300_000
_MAX_ITEMS = 600


def first_string(obj: dict[str, Any], keys: set[str]) -> str | None:
    """First string value whose key is in `keys`, skipping url/id-looking values."""
    import re

    for raw_key, value in obj.items():
        if isinstance(raw_key, str) and raw_key.lower() in keys and isinstance(value, str) and value.strip():
            if value.startswith(("http://", "https://", "/")) or re.fullmatch(r"[0-9a-f-]{8,}", value):
                continue
            return value
    return None


def json_blobs(html: str, *, soup: Any = None) -> list[str]:
    """All embedded JSON payloads: `<script id=__NEXT_DATA__>` / `type=application/json`
    blobs PLUS inline `window.__NUXT__ = {...}`-style state assignments (brace-matched).
    Schema.org JSON-LD is skipped -- it has a dedicated extractor. ``soup`` lets a caller
    that already parsed this HTML reuse the tree instead of re-parsing it."""
    if soup is None:
        soup = make_soup(html)
    blobs: list[str] = []
    for script in soup.find_all("script"):
        script_type = (script.get("type") or "").lower()
        text = script.string or script.get_text() or ""
        if not text:
            continue
        if "ld+json" not in script_type and (
            script.get("id") == "__NEXT_DATA__" or script_type == "application/json"
        ):
            stripped = text.strip()
            if stripped.startswith(("{", "[")):
                blobs.append(stripped)
            continue
        for var in _STATE_VARS:
            idx = text.find(var)
            if idx == -1:
                continue
            eq = text.find("=", idx)
            if eq == -1:
                continue
            blob = _balanced_json(text, eq + 1)
            if blob:
                blobs.append(blob)
                break
    return blobs


def _balanced_json(text: str, start: int) -> str | None:
    """Extract a balanced {...}/[...] starting at/after `start`, string-aware so
    braces inside strings don't miscount."""
    n = len(text)
    i = start
    while i < n and text[i] in " \t\r\n":
        i += 1
    if i >= n or text[i] not in "{[":
        return None
    open_char = text[i]
    close_char = "}" if open_char == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    quote = ""
    j = i
    while j < n:
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
        elif ch in "\"'":
            in_str = True
            quote = ch
        elif ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
        j += 1
    return None


def _accumulate(node, item_fn, key_fn, out, seen, budget, max_items) -> None:
    if budget[0] <= 0 or len(out) >= max_items:
        return
    budget[0] -= 1
    if isinstance(node, list):
        for child in node:
            _accumulate(child, item_fn, key_fn, out, seen, budget, max_items)
        return
    if not isinstance(node, dict):
        return
    record = item_fn(node)
    if record is not None:
        key = key_fn(record)
        if key not in seen:
            seen.add(key)
            out.append(record)
    for value in node.values():
        if isinstance(value, (list, dict)):
            _accumulate(value, item_fn, key_fn, out, seen, budget, max_items)


def extract_records_from_html(
    html: str,
    *,
    item_fn: Callable[[dict], Any],
    key_fn: Callable[[Any], Any],
    max_items: int = _MAX_ITEMS,
    soup: Any = None,
) -> list:
    """Harvest every embedded JSON blob and walk it, building deduped records.
    ``soup`` lets a caller reuse an already-parsed tree (avoids re-parsing the HTML)."""
    out: list = []
    seen: set = set()
    for blob in json_blobs(html, soup=soup):
        try:
            payload = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        _accumulate(payload, item_fn, key_fn, out, seen, [_MAX_NODES], max_items)
        if len(out) >= max_items:
            break
    return out[:max_items]


def extract_records_from_obj(
    payload: Any,
    *,
    item_fn: Callable[[dict], Any],
    key_fn: Callable[[Any], Any],
    max_items: int = _MAX_ITEMS,
) -> list:
    """Walk an already-parsed JSON object (e.g. an API response)."""
    out: list = []
    seen: set = set()
    _accumulate(payload, item_fn, key_fn, out, seen, [_MAX_NODES], max_items)
    return out[:max_items]
