from __future__ import annotations

import json
from typing import Any

from safeplate.soup import make_soup


def json_ld_items_from_html(html: str) -> list[dict[str, Any]]:
    return json_ld_items_from_soup(make_soup(html))


def json_ld_items_from_soup(soup: Any) -> list[dict[str, Any]]:
    items = []
    for script in soup.find_all("script", type=lambda value: value and "ld+json" in value):
        raw_json = script.string or script.get_text()
        if not raw_json or not raw_json.strip():
            continue
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            continue
        items.extend(flatten_json_ld(payload))
    return items


def flatten_json_ld(payload: Any) -> list[dict[str, Any]]:
    items = []
    if isinstance(payload, list):
        for value in payload:
            items.extend(flatten_json_ld(value))
        return items

    if not isinstance(payload, dict):
        return items

    items.append(payload)
    graph = payload.get("@graph")
    if isinstance(graph, list):
        for value in graph:
            items.extend(flatten_json_ld(value))
    return items
