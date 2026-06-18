from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from safeplate.coerce import float_value as _float_value
from safeplate.coerce import int_value as _int_value
from safeplate.coerce import split_semicolon_terms as _split_terms
from safeplate.io import read_csv_rows as _read_csv_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a polished local SafePlate dashboard from CSV outputs."
    )
    parser.add_argument("--menu-items-csv")
    parser.add_argument("--menu-text-csv")
    parser.add_argument("--restaurants-csv")
    parser.add_argument("--out-dir", default="data")
    parser.add_argument("--title", default="SafePlate Data Studio")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = ROOT / "data"
    menu_items_path = _path_or_latest(
        args.menu_items_csv,
        data_dir,
        "menu_items_*.csv",
        exclude_suffixes=("_items.csv",),
    )
    menu_text_path = _path_or_latest(args.menu_text_csv, data_dir, "menu_text_*.csv")
    restaurants_path = _path_or_latest(
        args.restaurants_csv,
        data_dir,
        "restaurants*.csv",
        required=False,
    )

    menu_items = _read_csv(menu_items_path)
    menu_text = _read_csv(menu_text_path) if menu_text_path else []
    restaurants = _read_csv(restaurants_path) if restaurants_path else []
    dashboard_data = _build_dashboard_data(
        title=args.title,
        menu_items_path=menu_items_path,
        menu_text_path=menu_text_path,
        restaurants_path=restaurants_path,
        menu_items=menu_items,
        menu_text=menu_text,
        restaurants=restaurants,
    )

    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y_%m_%d_%H%M%S")
    out_path = out_dir / f"safeplate_dashboard_{stamp}.html"
    out_path.write_text(_render_html(dashboard_data), encoding="utf-8")
    print(f"Dashboard: {out_path}")


def _path_or_latest(
    value: str | None,
    data_dir: Path,
    pattern: str,
    *,
    required: bool = True,
    exclude_suffixes: tuple[str, ...] = (),
) -> Path | None:
    if value:
        return Path(value)

    candidates = [
        path
        for path in data_dir.glob(pattern)
        if not any(path.name.endswith(suffix) for suffix in exclude_suffixes)
    ]
    if not candidates:
        if required:
            raise SystemExit(f"No data file found for pattern: {pattern}")
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _read_csv(path: Path | None) -> list[dict[str, str]]:
    if not path:
        return []
    return _read_csv_rows(path)


def _build_dashboard_data(
    *,
    title: str,
    menu_items_path: Path,
    menu_text_path: Path | None,
    restaurants_path: Path | None,
    menu_items: list[dict[str, str]],
    menu_text: list[dict[str, str]],
    restaurants: list[dict[str, str]],
) -> dict[str, Any]:
    metadata_by_source_id = {
        row.get("source_id", ""): row
        for row in restaurants
        if row.get("source_id")
    }
    text_by_restaurant = _group_by(menu_text, "restaurant_name")
    grouped_items = _group_by(menu_items, "restaurant_name")

    restaurant_cards = []
    normalized_items = []
    for restaurant_name, rows in grouped_items.items():
        source_id = rows[0].get("restaurant_source_id", "")
        metadata = metadata_by_source_id.get(source_id, {})
        text_rows = text_by_restaurant.get(restaurant_name, [])
        method_counts = _count_by(rows, "extraction_method")
        source_type_counts = _count_by(rows, "source_type")
        category_counts = _count_by(rows, "category")
        dietary_count = sum(1 for row in rows if row.get("dietary_terms", "").strip())
        allergen_count = sum(1 for row in rows if row.get("allergen_terms", "").strip())
        prices_count = sum(1 for row in rows if row.get("price", "").strip())
        confidence_values = [_float_value(row.get("confidence")) for row in rows]
        avg_confidence = (
            sum(confidence_values) / len(confidence_values)
            if confidence_values
            else 0
        )

        restaurant_cards.append(
            {
                "name": restaurant_name,
                "sourceId": source_id,
                "website": rows[0].get("menu_source_url", ""),
                "rating": metadata.get("rating", ""),
                "reviewCount": metadata.get("review_count", ""),
                "address": metadata.get("address", ""),
                "itemCount": len(rows),
                "priceCount": prices_count,
                "dietaryCount": dietary_count,
                "allergenCount": allergen_count,
                "avgConfidence": round(avg_confidence, 2),
                "methodCounts": method_counts,
                "sourceTypeCounts": source_type_counts,
                "categoryCounts": category_counts,
                "textRecords": len(text_rows),
                "textChars": sum(_int_value(row.get("char_count")) for row in text_rows),
                "priceHits": sum(_int_value(row.get("price_count")) for row in text_rows),
            }
        )

        for index, row in enumerate(rows):
            normalized_items.append(
                {
                    "id": f"{_slugify(restaurant_name)}-{index}",
                    "restaurant": restaurant_name,
                    "category": row.get("category", "") or "Uncategorized",
                    "name": row.get("item_name", ""),
                    "description": row.get("description", ""),
                    "price": row.get("price", ""),
                    "dietaryTerms": _split_terms(row.get("dietary_terms", "")),
                    "allergenTerms": _split_terms(row.get("allergen_terms", "")),
                    "sourceType": row.get("source_type", ""),
                    "method": row.get("extraction_method", ""),
                    "confidence": _float_value(row.get("confidence")),
                    "rawText": row.get("raw_text", ""),
                    "url": row.get("menu_source_url", ""),
                }
            )

    restaurant_cards.sort(key=lambda row: (-row["itemCount"], row["name"].lower()))
    total_items = len(normalized_items)
    method_counts = _count_by(menu_items, "extraction_method")
    category_counts = _count_by(menu_items, "category")
    total_prices = sum(1 for row in menu_items if row.get("price", "").strip())
    total_dietary = sum(1 for row in menu_items if row.get("dietary_terms", "").strip())
    total_allergen = sum(1 for row in menu_items if row.get("allergen_terms", "").strip())

    return {
        "title": title,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "files": {
            "menuItems": str(menu_items_path),
            "menuText": str(menu_text_path) if menu_text_path else "",
            "restaurants": str(restaurants_path) if restaurants_path else "",
        },
        "summary": {
            "restaurants": len(restaurant_cards),
            "items": total_items,
            "pricedItems": total_prices,
            "dietaryRows": total_dietary,
            "allergenRows": total_allergen,
            "schemaRows": method_counts.get("schema_org_menu_item", 0),
            "htmlRows": method_counts.get("html_visible_text", 0),
            "methods": method_counts,
            "categories": category_counts,
        },
        "restaurants": restaurant_cards,
        "items": normalized_items,
    }


def _group_by(rows: list[dict[str, str]], field: str) -> dict[str, list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        key = row.get(field, "").strip() or "Unknown"
        groups.setdefault(key, []).append(row)
    return groups


def _count_by(rows: list[dict[str, str]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = row.get(field, "").strip() or "Unknown"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0].lower())))


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    return cleaned.strip("-") or "row"


def _render_html(data: dict[str, Any]) -> str:
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = escape(str(data["title"]))
    return DASHBOARD_HTML.replace("__TITLE__", title).replace("__DATA_JSON__", data_json)


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>__TITLE__</title>
  <style>
    :root {
      color-scheme: light;
      --paper: #fbfaf7;
      --panel: #ffffff;
      --ink: #17211d;
      --muted: #6b746f;
      --line: #e5e1d8;
      --green: #246858;
      --mint: #dcefe7;
      --blue: #3c67c8;
      --blue-soft: #e6edff;
      --coral: #c85636;
      --coral-soft: #f6e4dc;
      --violet: #7d5bb2;
      --violet-soft: #eee6fa;
      --yellow: #c4921d;
      --yellow-soft: #fbefd1;
      --shadow: 0 14px 34px rgba(23, 33, 29, .09);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        linear-gradient(180deg, rgba(220,239,231,.9), rgba(251,250,247,0) 340px),
        var(--paper);
      color: var(--ink);
      letter-spacing: 0;
    }
    button, input { font: inherit; }
    .app { min-height: 100vh; padding: 18px; }
    .shell {
      display: grid;
      grid-template-columns: 310px minmax(0, 1fr);
      gap: 14px;
      max-width: 1480px;
      margin: 0 auto;
    }
    .topbar {
      grid-column: 1 / -1;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      padding: 14px 16px;
      background: rgba(255,255,255,.78);
      border: 1px solid rgba(229,225,216,.95);
      border-radius: 8px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
    }
    .brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
    .mark {
      width: 42px;
      height: 42px;
      border-radius: 8px;
      border: 1px solid rgba(36,104,88,.2);
      background:
        radial-gradient(circle at 50% 50%, #fff 0 27%, transparent 28%),
        conic-gradient(from 120deg, var(--green), var(--blue), var(--coral), var(--green));
      box-shadow: inset 0 0 0 7px #f5fbf8;
      flex: 0 0 auto;
    }
    h1, h2, h3, p { margin: 0; }
    h1 { font-size: 19px; line-height: 1.1; }
    .subtitle { color: var(--muted); font-size: 13px; margin-top: 4px; }
    .status-strip {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      min-height: 28px;
      padding: 5px 9px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .pill strong { color: var(--ink); font-weight: 700; }
    .sidebar, .panel, .metric, .item-row {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,.86);
      box-shadow: var(--shadow);
    }
    .sidebar {
      position: sticky;
      top: 18px;
      height: calc(100vh - 112px);
      min-height: 620px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
    .sidebar-head { padding: 14px; border-bottom: 1px solid var(--line); }
    .search {
      width: 100%;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 8px;
      padding: 10px 11px;
      outline: none;
    }
    .search:focus { border-color: rgba(36,104,88,.55); box-shadow: 0 0 0 3px rgba(36,104,88,.12); }
    .restaurant-list { overflow: auto; padding: 8px; display: grid; gap: 8px; }
    .restaurant-button {
      border: 1px solid transparent;
      background: transparent;
      border-radius: 8px;
      padding: 10px;
      text-align: left;
      cursor: pointer;
      display: grid;
      gap: 8px;
      color: var(--ink);
    }
    .restaurant-button:hover { background: #f6f4ef; }
    .restaurant-button.active {
      background: var(--mint);
      border-color: rgba(36,104,88,.28);
    }
    .restaurant-title-row {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: baseline;
    }
    .restaurant-name {
      font-size: 14px;
      font-weight: 800;
      overflow-wrap: anywhere;
    }
    .count { color: var(--green); font-size: 13px; font-weight: 800; }
    .micro { color: var(--muted); font-size: 12px; line-height: 1.35; }
    .mini-bars { display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px; height: 5px; }
    .mini-bars span { border-radius: 99px; background: var(--line); }
    .main { min-width: 0; display: grid; gap: 14px; }
    .metrics {
      display: grid;
      grid-template-columns: repeat(5, minmax(120px, 1fr));
      gap: 10px;
    }
    .metric { padding: 13px; min-height: 92px; display: grid; align-content: space-between; }
    .metric-label { color: var(--muted); font-size: 12px; font-weight: 700; }
    .metric-value { font-size: 28px; line-height: 1; font-weight: 850; margin-top: 8px; }
    .metric-note { color: var(--muted); font-size: 12px; }
    .workspace {
      display: grid;
      grid-template-columns: minmax(0, 1.1fr) minmax(360px, .9fr);
      gap: 14px;
    }
    .panel { overflow: hidden; }
    .panel-head {
      padding: 16px;
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      align-items: start;
      gap: 12px;
    }
    .panel-title { font-size: 16px; font-weight: 850; }
    .panel-subtitle { color: var(--muted); font-size: 12px; margin-top: 5px; line-height: 1.45; }
    .panel-body { padding: 16px; }
    .restaurant-hero {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 150px;
      gap: 14px;
      align-items: stretch;
    }
    .score-card {
      border: 1px solid rgba(36,104,88,.24);
      border-radius: 8px;
      background: linear-gradient(145deg, #ffffff, #eef8f3);
      padding: 14px;
    }
    .confidence {
      display: grid;
      place-items: center;
      min-height: 132px;
      border-radius: 8px;
      background:
        conic-gradient(var(--green) calc(var(--score) * 1%), #e5e1d8 0);
      position: relative;
    }
    .confidence::after {
      content: "";
      position: absolute;
      inset: 12px;
      border-radius: 8px;
      background: #fff;
    }
    .confidence-value {
      position: relative;
      z-index: 1;
      text-align: center;
      font-weight: 850;
      font-size: 24px;
    }
    .confidence-value span {
      display: block;
      color: var(--muted);
      font-weight: 700;
      font-size: 11px;
      margin-top: 4px;
    }
    .bar-list { display: grid; gap: 12px; }
    .bar-row {
      display: grid;
      grid-template-columns: 138px minmax(0, 1fr) 42px;
      gap: 10px;
      align-items: center;
      font-size: 12px;
    }
    .track {
      height: 10px;
      border-radius: 999px;
      background: #f0ede7;
      overflow: hidden;
    }
    .fill { height: 100%; border-radius: inherit; background: var(--green); width: var(--width); }
    .fill.schema { background: var(--green); }
    .fill.html { background: var(--blue); }
    .fill.pdf { background: var(--coral); }
    .fill.ocr { background: var(--violet); }
    .categories {
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
    }
    .category-chip {
      padding: 7px 9px;
      border-radius: 8px;
      background: #f7f4ec;
      border: 1px solid var(--line);
      font-size: 12px;
      color: var(--muted);
    }
    .category-chip strong { color: var(--ink); }
    .toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
      background: #fffdfa;
      flex-wrap: wrap;
    }
    .segments {
      display: inline-flex;
      gap: 3px;
      padding: 3px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f4f1eb;
    }
    .segment {
      border: 0;
      border-radius: 6px;
      padding: 7px 10px;
      background: transparent;
      color: var(--muted);
      cursor: pointer;
      font-size: 12px;
      font-weight: 800;
    }
    .segment.active {
      background: #fff;
      color: var(--ink);
      box-shadow: 0 2px 10px rgba(23,33,29,.08);
    }
    .items {
      padding: 10px;
      display: grid;
      gap: 8px;
      max-height: 680px;
      overflow: auto;
    }
    .item-row {
      box-shadow: none;
      padding: 11px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: start;
    }
    .item-main { min-width: 0; }
    .item-name {
      font-size: 14px;
      font-weight: 850;
      overflow-wrap: anywhere;
    }
    .item-desc {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
      margin-top: 5px;
      overflow-wrap: anywhere;
    }
    .item-price {
      min-width: 52px;
      border-radius: 8px;
      background: var(--mint);
      color: var(--green);
      text-align: center;
      padding: 7px 8px;
      font-weight: 850;
      font-size: 13px;
    }
    .tags { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }
    .tag {
      border-radius: 8px;
      padding: 4px 7px;
      font-size: 11px;
      color: var(--muted);
      background: #f5f3ee;
      border: 1px solid var(--line);
    }
    .tag.schema { background: var(--mint); color: var(--green); border-color: rgba(36,104,88,.16); }
    .tag.html { background: var(--blue-soft); color: var(--blue); border-color: rgba(60,103,200,.16); }
    .tag.dietary { background: var(--yellow-soft); color: #79570e; border-color: rgba(196,146,29,.18); }
    .tag.allergen { background: var(--coral-soft); color: var(--coral); border-color: rgba(200,86,54,.18); }
    .empty {
      padding: 28px;
      color: var(--muted);
      text-align: center;
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: #fff;
    }
    .fileline {
      color: var(--muted);
      font-size: 11px;
      overflow-wrap: anywhere;
    }
    @media (max-width: 1100px) {
      .shell { grid-template-columns: 1fr; }
      .sidebar { position: static; height: auto; min-height: 0; }
      .restaurant-list { grid-template-columns: repeat(2, minmax(0, 1fr)); max-height: 360px; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .workspace { grid-template-columns: 1fr; }
    }
    @media (max-width: 680px) {
      .app { padding: 10px; }
      .topbar { align-items: start; flex-direction: column; }
      .status-strip { justify-content: flex-start; }
      .restaurant-list { grid-template-columns: 1fr; }
      .metrics { grid-template-columns: 1fr; }
      .restaurant-hero { grid-template-columns: 1fr; }
      .bar-row { grid-template-columns: 104px minmax(0, 1fr) 36px; }
      .item-row { grid-template-columns: 1fr; }
      .item-price { width: fit-content; }
    }
  </style>
</head>
<body>
  <div class="app">
    <div class="shell">
      <header class="topbar">
        <div class="brand">
          <div class="mark" aria-hidden="true"></div>
          <div>
            <h1 id="appTitle"></h1>
            <p class="subtitle" id="snapshotLine"></p>
          </div>
        </div>
        <div class="status-strip" id="statusStrip"></div>
      </header>

      <aside class="sidebar">
        <div class="sidebar-head">
          <input id="restaurantSearch" class="search" type="search" placeholder="Search restaurants" />
        </div>
        <div id="restaurantList" class="restaurant-list"></div>
      </aside>

      <main class="main">
        <section class="metrics" id="metrics"></section>

        <section class="workspace">
          <div class="panel">
            <div class="panel-head">
              <div>
                <h2 class="panel-title" id="restaurantTitle"></h2>
                <p class="panel-subtitle" id="restaurantMeta"></p>
              </div>
              <span class="pill"><strong id="restaurantItems"></strong> rows</span>
            </div>
            <div class="panel-body">
              <div class="restaurant-hero">
                <div class="score-card">
                  <h3 class="panel-title">Evidence Mix</h3>
                  <p class="panel-subtitle" id="methodSubtitle"></p>
                  <div class="bar-list" id="methodBars" style="margin-top: 14px;"></div>
                </div>
                <div class="confidence" id="confidenceDial">
                  <div class="confidence-value" id="confidenceValue"></div>
                </div>
              </div>
            </div>
          </div>

          <div class="panel">
            <div class="panel-head">
              <div>
                <h2 class="panel-title">Category Coverage</h2>
                <p class="panel-subtitle">Menu sections found in the current extraction.</p>
              </div>
            </div>
            <div class="panel-body">
              <div class="categories" id="categoryChips"></div>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="toolbar">
            <div class="segments" id="segments"></div>
            <input id="itemSearch" class="search" type="search" placeholder="Search menu items" style="max-width: 320px;" />
          </div>
          <div class="items" id="items"></div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div>
              <h2 class="panel-title">Run Files</h2>
              <p class="panel-subtitle">Current dashboard inputs.</p>
            </div>
          </div>
          <div class="panel-body" id="files"></div>
        </section>
      </main>
    </div>
  </div>

  <script>
    window.safeplateData = __DATA_JSON__;
    const data = window.safeplateData;
    const state = {
      restaurant: data.restaurants[0]?.name || "",
      restaurantQuery: "",
      itemQuery: "",
      filter: "all",
    };
    const filters = [
      ["all", "All"],
      ["schema", "Schema"],
      ["html", "HTML"],
      ["dietary", "Dietary"],
      ["allergen", "Allergen"],
    ];

    const methodNames = {
      schema_org_menu_item: "Schema.org",
      html_visible_text: "HTML text",
      pdf_text: "PDF",
      openai_vision_text: "Vision",
      easyocr_text: "EasyOCR",
      image_ocr: "Tesseract",
    };

    function fmt(value) {
      return new Intl.NumberFormat().format(value || 0);
    }

    function methodLabel(value) {
      return methodNames[value] || value || "Unknown";
    }

    function methodClass(value) {
      if (value.includes("schema")) return "schema";
      if (value.includes("html")) return "html";
      if (value.includes("pdf")) return "pdf";
      if (value.includes("ocr") || value.includes("vision")) return "ocr";
      return "";
    }

    function selectedRestaurant() {
      return data.restaurants.find((row) => row.name === state.restaurant) || data.restaurants[0];
    }

    function restaurantItems() {
      return data.items.filter((row) => row.restaurant === state.restaurant);
    }

    function filteredItems() {
      const query = state.itemQuery.trim().toLowerCase();
      return restaurantItems().filter((row) => {
        if (state.filter === "schema" && row.method !== "schema_org_menu_item") return false;
        if (state.filter === "html" && row.method !== "html_visible_text") return false;
        if (state.filter === "dietary" && row.dietaryTerms.length === 0) return false;
        if (state.filter === "allergen" && row.allergenTerms.length === 0) return false;
        if (!query) return true;
        return [row.name, row.description, row.category, row.rawText].join(" ").toLowerCase().includes(query);
      });
    }

    function renderTop() {
      document.getElementById("appTitle").textContent = data.title;
      document.getElementById("snapshotLine").textContent = `Generated ${new Date(data.generatedAt).toLocaleString()} from ${data.summary.restaurants} restaurants`;
      document.getElementById("statusStrip").innerHTML = [
        ["Structured", data.summary.schemaRows],
        ["HTML", data.summary.htmlRows],
        ["Priced", data.summary.pricedItems],
      ].map(([label, value]) => `<span class="pill"><strong>${fmt(value)}</strong>${label}</span>`).join("");
    }

    function renderMetrics() {
      const metrics = [
        ["Restaurants", data.summary.restaurants, "with menu evidence"],
        ["Menu rows", data.summary.items, "candidate items"],
        ["Schema rows", data.summary.schemaRows, "official structured data"],
        ["Dietary signals", data.summary.dietaryRows, "rows with diet terms"],
        ["Allergen signals", data.summary.allergenRows, "rows with allergen terms"],
      ];
      document.getElementById("metrics").innerHTML = metrics.map(([label, value, note]) => `
        <div class="metric">
          <div class="metric-label">${label}</div>
          <div class="metric-value">${fmt(value)}</div>
          <div class="metric-note">${note}</div>
        </div>
      `).join("");
    }

    function renderRestaurants() {
      const query = state.restaurantQuery.trim().toLowerCase();
      const rows = data.restaurants.filter((row) => row.name.toLowerCase().includes(query));
      document.getElementById("restaurantList").innerHTML = rows.map((row) => {
        const schema = row.methodCounts.schema_org_menu_item || 0;
        const html = row.methodCounts.html_visible_text || 0;
        const total = Math.max(row.itemCount, 1);
        return `
          <button class="restaurant-button ${row.name === state.restaurant ? "active" : ""}" data-restaurant="${escapeHtml(row.name)}">
            <div class="restaurant-title-row">
              <div class="restaurant-name">${escapeHtml(row.name)}</div>
              <div class="count">${fmt(row.itemCount)}</div>
            </div>
            <div class="micro">${fmt(schema)} schema · ${fmt(html)} html · ${fmt(row.allergenCount)} allergen rows</div>
            <div class="mini-bars">
              <span style="background: var(--green); transform: scaleX(${schema / total}); transform-origin: left;"></span>
              <span style="background: var(--blue); transform: scaleX(${html / total}); transform-origin: left;"></span>
              <span style="background: var(--coral); transform: scaleX(${row.allergenCount / total}); transform-origin: left;"></span>
            </div>
          </button>
        `;
      }).join("") || `<div class="empty">No restaurants match the search.</div>`;

      document.querySelectorAll("[data-restaurant]").forEach((button) => {
        button.addEventListener("click", () => {
          state.restaurant = button.dataset.restaurant;
          render();
        });
      });
    }

    function renderRestaurantDetail() {
      const restaurant = selectedRestaurant();
      if (!restaurant) return;
      document.getElementById("restaurantTitle").textContent = restaurant.name;
      const rating = restaurant.rating ? `${restaurant.rating} rating` : "rating not in this run";
      const reviews = restaurant.reviewCount ? `${restaurant.reviewCount} reviews` : "review count unavailable";
      document.getElementById("restaurantMeta").textContent = `${rating} · ${reviews} · ${restaurant.website || "menu URL unavailable"}`;
      document.getElementById("restaurantItems").textContent = fmt(restaurant.itemCount);
      document.getElementById("methodSubtitle").textContent = `${fmt(restaurant.priceCount)} priced rows · ${fmt(restaurant.textChars)} extracted characters · ${fmt(restaurant.priceHits)} price hits`;
      const score = Math.round((restaurant.avgConfidence || 0) * 100);
      const dial = document.getElementById("confidenceDial");
      dial.style.setProperty("--score", String(score));
      document.getElementById("confidenceValue").innerHTML = `${score}<span>avg confidence</span>`;

      const methods = Object.entries(restaurant.methodCounts);
      const methodTotal = Math.max(restaurant.itemCount, 1);
      document.getElementById("methodBars").innerHTML = methods.map(([method, count]) => `
        <div class="bar-row">
          <span>${escapeHtml(methodLabel(method))}</span>
          <span class="track"><span class="fill ${methodClass(method)}" style="--width: ${(count / methodTotal) * 100}%"></span></span>
          <strong>${fmt(count)}</strong>
        </div>
      `).join("");

      const categories = Object.entries(restaurant.categoryCounts).slice(0, 24);
      document.getElementById("categoryChips").innerHTML = categories.map(([category, count]) => `
        <span class="category-chip"><strong>${fmt(count)}</strong> ${escapeHtml(category)}</span>
      `).join("") || `<div class="empty">No categories found.</div>`;
    }

    function renderSegments() {
      document.getElementById("segments").innerHTML = filters.map(([key, label]) => `
        <button class="segment ${state.filter === key ? "active" : ""}" data-filter="${key}">${label}</button>
      `).join("");
      document.querySelectorAll("[data-filter]").forEach((button) => {
        button.addEventListener("click", () => {
          state.filter = button.dataset.filter;
          renderItems();
          renderSegments();
        });
      });
    }

    function renderItems() {
      const rows = filteredItems();
      document.getElementById("items").innerHTML = rows.map((row) => {
        const terms = [
          `<span class="tag ${methodClass(row.method)}">${escapeHtml(methodLabel(row.method))}</span>`,
          `<span class="tag">${escapeHtml(row.category)}</span>`,
          ...row.dietaryTerms.map((term) => `<span class="tag dietary">${escapeHtml(term)}</span>`),
          ...row.allergenTerms.map((term) => `<span class="tag allergen">${escapeHtml(term)}</span>`),
        ].join("");
        return `
          <article class="item-row">
            <div class="item-main">
              <div class="item-name">${escapeHtml(row.name || "Unnamed item")}</div>
              <div class="item-desc">${escapeHtml(row.description || row.rawText || "No description captured")}</div>
              <div class="tags">${terms}</div>
            </div>
            <div class="item-price">${escapeHtml(row.price || "—")}</div>
          </article>
        `;
      }).join("") || `<div class="empty">No menu rows match the current filter.</div>`;
    }

    function renderFiles() {
      const rows = Object.entries(data.files).filter(([, value]) => value);
      document.getElementById("files").innerHTML = rows.map(([label, value]) => `
        <p class="fileline"><strong>${escapeHtml(label)}</strong>: ${escapeHtml(value)}</p>
      `).join("");
    }

    function escapeHtml(value) {
      return String(value || "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[char]));
    }

    function render() {
      renderTop();
      renderMetrics();
      renderRestaurants();
      renderRestaurantDetail();
      renderSegments();
      renderItems();
      renderFiles();
    }

    document.getElementById("restaurantSearch").addEventListener("input", (event) => {
      state.restaurantQuery = event.target.value;
      renderRestaurants();
    });
    document.getElementById("itemSearch").addEventListener("input", (event) => {
      state.itemQuery = event.target.value;
      renderItems();
    });

    render();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
