from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import urlparse

from safeplate.coerce import int_value as _int_value
from safeplate.io import read_csv_rows


IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp", ".gif"]


def build_report_path(csv_path: Path, out_dir: Path | None = None) -> Path:
    target_dir = out_dir or csv_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / f"{csv_path.stem}.report.html"


def write_menu_sources_report(
    *,
    csv_path: Path,
    html_path: Path,
    title: str | None = None,
    restaurants_csv_path: Path | None = None,
) -> None:
    rows = read_csv_rows(csv_path)
    restaurant_rows = read_csv_rows(restaurants_csv_path) if restaurants_csv_path else []
    html = _render_menu_sources_report(
        rows=rows,
        restaurant_rows=restaurant_rows,
        title=title or csv_path.stem,
        source_file=csv_path,
    )
    html_path.write_text(html, encoding="utf-8")


def build_extraction_report_path(
    item_csv_path: Path,
    out_dir: Path | None = None,
) -> Path:
    target_dir = out_dir or item_csv_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / f"{item_csv_path.stem}.report.html"


def write_menu_extraction_report(
    *,
    text_csv_path: Path,
    item_csv_path: Path,
    html_path: Path,
    title: str | None = None,
) -> None:
    text_rows = read_csv_rows(text_csv_path)
    item_rows = read_csv_rows(item_csv_path)
    html = _render_menu_extraction_report(
        text_rows=text_rows,
        item_rows=item_rows,
        title=title or item_csv_path.stem,
        text_file=text_csv_path,
        item_file=item_csv_path,
    )
    html_path.write_text(html, encoding="utf-8")


def _render_menu_sources_report(
    *,
    rows: list[dict[str, str]],
    restaurant_rows: list[dict[str, str]],
    title: str,
    source_file: Path,
) -> str:
    generated_at = datetime.now(timezone.utc).isoformat()
    coverage = _restaurant_coverage(rows, restaurant_rows)
    source_counts = _count_by(rows, "source_type")
    validation_counts = _count_by(rows, "validation_status")
    best_rows = _best_menu_rows_by_restaurant(rows)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} - SafePlate Menu Sources</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f8faf7;
      --ink: #1f2933;
      --muted: #667085;
      --line: #d9e2dd;
      --panel: #ffffff;
      --good: #126b48;
      --warn: #915c00;
      --bad: #9f1f2f;
      --chip: #eef5f0;
      --link: #125f8f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Arial, Helvetica, sans-serif;
      font-size: 14px;
      line-height: 1.45;
    }}
    header {{
      padding: 24px 28px 14px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 24px;
      letter-spacing: 0;
    }}
    .meta {{
      color: var(--muted);
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
    }}
    main {{
      padding: 20px 28px 32px;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 10px;
      margin-bottom: 18px;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 12px;
    }}
    .metric strong {{
      display: block;
      font-size: 22px;
      margin-bottom: 2px;
    }}
    .metric span {{ color: var(--muted); }}
    .table-wrap {{
      overflow-x: auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 6px;
    }}
    table {{
      width: 100%;
      min-width: 1180px;
      border-collapse: collapse;
    }}
    th, td {{
      padding: 10px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
      text-align: left;
    }}
    th {{
      position: sticky;
      top: 0;
      background: #f2f6f3;
      z-index: 1;
      color: #344054;
      font-size: 12px;
      text-transform: uppercase;
    }}
    tr:hover {{ background: #fbfdfb; }}
    a {{ color: var(--link); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .badge {{
      display: inline-block;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      background: var(--chip);
      color: #344054;
      white-space: nowrap;
    }}
    .validated {{ color: var(--good); background: #e9f7ef; }}
    .unvalidated {{ color: var(--warn); background: #fff4df; }}
    .not_fetchable {{ color: var(--bad); background: #fdecef; }}
    .confidence {{
      font-variant-numeric: tabular-nums;
      font-weight: 700;
    }}
    .preview {{
      width: 140px;
      min-height: 60px;
      display: flex;
      align-items: center;
      justify-content: center;
      background: #f5f7f6;
      border: 1px solid var(--line);
      border-radius: 4px;
      overflow: hidden;
      color: var(--muted);
      font-size: 12px;
      text-align: center;
    }}
    .preview img {{
      width: 100%;
      height: 100px;
      object-fit: cover;
      display: block;
    }}
    .small {{
      color: var(--muted);
      font-size: 12px;
    }}
    .url {{
      max-width: 320px;
      word-break: break-word;
    }}
    h2 {{
      font-size: 18px;
      margin: 22px 0 10px;
    }}
  </style>
</head>
<body>
  <header>
    <h1>{escape(title)}</h1>
    <div class="meta">
      <span>Source: {escape(str(source_file))}</span>
      <span>Generated: {escape(generated_at)}</span>
    </div>
  </header>
  <main>
    <section class="summary">
      {_metric("Candidates", str(len(rows)))}
      {_metric("Restaurants With Candidates", str(coverage["restaurants_with_candidates"]))}
      {_metric("Restaurants With Validated Candidates", str(coverage["restaurants_with_validated_candidates"]))}
      {_metric("Restaurants Inspected", str(coverage["restaurants_inspected"]))}
      {_metric("Types", _inline_counts(source_counts))}
      {_metric("Validation", _inline_counts(validation_counts))}
    </section>
    <h2>Best Menu Link Per Restaurant</h2>
    <section class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Restaurant</th>
            <th>Grade</th>
            <th>Type</th>
            <th>Confidence</th>
            <th>Status</th>
            <th>Best Candidate</th>
            <th>Why This Link</th>
          </tr>
        </thead>
        <tbody>
          {''.join(_render_best_menu_row(row) for row in best_rows)}
        </tbody>
      </table>
    </section>
    <h2>All Menu Candidates</h2>
    <section class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Preview</th>
            <th>Restaurant</th>
            <th>Grade</th>
            <th>Type</th>
            <th>Confidence</th>
            <th>Status</th>
            <th>Candidate</th>
            <th>Link Text</th>
            <th>Reason</th>
            <th>Validation</th>
            <th>Website</th>
          </tr>
        </thead>
        <tbody>
          {''.join(_render_menu_row(row) for row in rows)}
        </tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""


def _render_menu_extraction_report(
    *,
    text_rows: list[dict[str, str]],
    item_rows: list[dict[str, str]],
    title: str,
    text_file: Path,
    item_file: Path,
) -> str:
    generated_at = datetime.now(timezone.utc).isoformat()
    text_methods = _text_contributions(text_rows, "extraction_method")
    text_sources = _text_contributions(text_rows, "source_type")
    item_methods = _item_contributions(item_rows, "extraction_method")
    item_sources = _item_contributions(item_rows, "source_type")
    restaurant_items = _count_by(item_rows, "restaurant_name")
    total_chars = sum(
        _int_value(row.get("char_count"), allow_float=True) for row in text_rows
    )
    restaurants_with_text = len({_restaurant_key(row) for row in text_rows if _restaurant_key(row)})
    restaurants_with_items = len({_restaurant_key(row) for row in item_rows if _restaurant_key(row)})

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} - SafePlate Menu Extraction</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f8faf7;
      --ink: #1f2933;
      --muted: #667085;
      --line: #d9e2dd;
      --panel: #ffffff;
      --chip: #eef5f0;
      --link: #125f8f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Arial, Helvetica, sans-serif;
      font-size: 14px;
      line-height: 1.45;
    }}
    header {{
      padding: 24px 28px 14px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
    }}
    h1 {{ margin: 0 0 8px; font-size: 24px; letter-spacing: 0; }}
    h2 {{ font-size: 18px; margin: 22px 0 10px; }}
    .meta {{
      color: var(--muted);
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
    }}
    main {{ padding: 20px 28px 32px; }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 10px;
      margin-bottom: 18px;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 12px;
    }}
    .metric strong {{ display: block; font-size: 22px; margin-bottom: 2px; }}
    .metric span {{ color: var(--muted); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
      gap: 14px;
      align-items: start;
    }}
    .table-wrap {{
      overflow-x: auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 6px;
    }}
    table {{ width: 100%; min-width: 760px; border-collapse: collapse; }}
    th, td {{
      padding: 10px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
      text-align: left;
    }}
    th {{
      background: #f2f6f3;
      color: #344054;
      font-size: 12px;
      text-transform: uppercase;
    }}
    a {{ color: var(--link); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .badge {{
      display: inline-block;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      background: var(--chip);
      color: #344054;
      white-space: nowrap;
    }}
    .url {{ max-width: 300px; word-break: break-word; }}
    .small {{ color: var(--muted); font-size: 12px; }}
  </style>
</head>
<body>
  <header>
    <h1>{escape(title)}</h1>
    <div class="meta">
      <span>Text source: {escape(str(text_file))}</span>
      <span>Item source: {escape(str(item_file))}</span>
      <span>Generated: {escape(generated_at)}</span>
    </div>
  </header>
  <main>
    <section class="summary">
      {_metric("Text Evidence Records", str(len(text_rows)))}
      {_metric("Menu Item Candidates", str(len(item_rows)))}
      {_metric("Restaurants With Text", str(restaurants_with_text))}
      {_metric("Restaurants With Items", str(restaurants_with_items))}
      {_metric("Extracted Characters", str(total_chars))}
      {_metric("Extraction Methods", _inline_counts(_count_by(text_rows + item_rows, "extraction_method")))}
    </section>
    <div class="grid">
      <section>
        <h2>Text Contribution By Method</h2>
        <div class="table-wrap">{_render_contribution_table(text_methods, text_mode=True)}</div>
      </section>
      <section>
        <h2>Item Contribution By Method</h2>
        <div class="table-wrap">{_render_contribution_table(item_methods, text_mode=False)}</div>
      </section>
      <section>
        <h2>Text Contribution By Source Type</h2>
        <div class="table-wrap">{_render_contribution_table(text_sources, text_mode=True)}</div>
      </section>
      <section>
        <h2>Item Contribution By Source Type</h2>
        <div class="table-wrap">{_render_contribution_table(item_sources, text_mode=False)}</div>
      </section>
    </div>
    <h2>Menu Items By Restaurant</h2>
    <section class="table-wrap">
      <table>
        <thead><tr><th>Restaurant</th><th>Item Candidates</th></tr></thead>
        <tbody>{''.join(_render_count_row(name, count) for name, count in sorted(restaurant_items.items(), key=lambda pair: pair[1], reverse=True))}</tbody>
      </table>
    </section>
    <h2>Sample Item Candidates</h2>
    <section class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Restaurant</th>
            <th>Method</th>
            <th>Source</th>
            <th>Category</th>
            <th>Item</th>
            <th>Price</th>
            <th>Signals</th>
            <th>Evidence</th>
          </tr>
        </thead>
        <tbody>{''.join(_render_item_row(row) for row in item_rows[:100])}</tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""


def _render_best_menu_row(row: dict[str, str]) -> str:
    candidate_url = row.get("candidate_url", "")
    status = row.get("validation_status", "")
    source_type = row.get("source_type", "")
    return f"""
          <tr>
            <td><strong>{escape(row.get("restaurant_name", "") or "Unknown")}</strong><div class="small">{escape(row.get("restaurant_source_id", ""))}</div></td>
            <td><span class="badge">{escape(row.get("evidence_grade", "") or "n/a")}</span></td>
            <td><span class="badge">{escape(source_type)}</span></td>
            <td class="confidence">{escape(row.get("confidence", ""))}</td>
            <td><span class="badge {escape(status)}">{escape(status or "n/a")}</span></td>
            <td class="url"><a href="{escape(candidate_url)}" target="_blank" rel="noreferrer">{escape(candidate_url)}</a></td>
            <td>{escape(row.get("reason", ""))}<div class="small">{escape(row.get("validation_reason", ""))}</div></td>
          </tr>
"""


def _render_menu_row(row: dict[str, str]) -> str:
    candidate_url = row.get("candidate_url", "")
    website_url = row.get("website_url", "")
    status = row.get("validation_status", "")
    source_type = row.get("source_type", "")

    return f"""
          <tr>
            <td>{_preview(candidate_url, source_type)}</td>
            <td><strong>{escape(row.get("restaurant_name", "") or "Unknown")}</strong><div class="small">{escape(row.get("restaurant_source_id", ""))}</div></td>
            <td><span class="badge">{escape(row.get("evidence_grade", "") or "n/a")}</span></td>
            <td><span class="badge">{escape(source_type)}</span></td>
            <td class="confidence">{escape(row.get("confidence", ""))}</td>
            <td><span class="badge {escape(status)}">{escape(status or "n/a")}</span></td>
            <td class="url"><a href="{escape(candidate_url)}" target="_blank" rel="noreferrer">{escape(candidate_url)}</a></td>
            <td>{escape(row.get("link_text", ""))}</td>
            <td>{escape(row.get("reason", ""))}</td>
            <td>{escape(row.get("validation_reason", ""))}</td>
            <td class="url"><a href="{escape(website_url)}" target="_blank" rel="noreferrer">{escape(website_url)}</a></td>
          </tr>
"""


def _preview(url: str, source_type: str) -> str:
    if source_type == "image" or _is_image_url(url):
        return f'<a class="preview" href="{escape(url)}" target="_blank" rel="noreferrer"><img src="{escape(url)}" alt="Candidate image preview" loading="lazy"></a>'
    label = "PDF" if urlparse(url).path.lower().endswith(".pdf") else "Open"
    return f'<a class="preview" href="{escape(url)}" target="_blank" rel="noreferrer">{label}</a>'


def _is_image_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(extension) for extension in IMAGE_EXTENSIONS)


def _count_by(rows: list[dict[str, str]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(field) or "n/a"
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _text_contributions(rows: list[dict[str, str]], field: str) -> dict[str, dict[str, int]]:
    contributions: dict[str, dict[str, int]] = {}
    for row in rows:
        key = row.get(field) or "n/a"
        bucket = contributions.setdefault(
            key,
            {"records": 0, "restaurants": set(), "chars": 0, "prices": 0},
        )
        bucket["records"] += 1
        if _restaurant_key(row):
            bucket["restaurants"].add(_restaurant_key(row))
        bucket["chars"] += _int_value(row.get("char_count"), allow_float=True)
        bucket["prices"] += _int_value(row.get("price_count"), allow_float=True)
    return _finalize_contributions(contributions)


def _item_contributions(rows: list[dict[str, str]], field: str) -> dict[str, dict[str, int]]:
    contributions: dict[str, dict[str, int]] = {}
    for row in rows:
        key = row.get(field) or "n/a"
        bucket = contributions.setdefault(
            key,
            {
                "records": 0,
                "restaurants": set(),
                "priced_items": 0,
                "dietary_items": 0,
                "allergen_items": 0,
            },
        )
        bucket["records"] += 1
        if _restaurant_key(row):
            bucket["restaurants"].add(_restaurant_key(row))
        if row.get("price"):
            bucket["priced_items"] += 1
        if row.get("dietary_terms"):
            bucket["dietary_items"] += 1
        if row.get("allergen_terms"):
            bucket["allergen_items"] += 1
    return _finalize_contributions(contributions)


def _finalize_contributions(
    contributions: dict[str, dict[str, int]],
) -> dict[str, dict[str, int]]:
    finalized = {}
    for key, values in contributions.items():
        finalized[key] = {
            name: (len(value) if isinstance(value, set) else value)
            for name, value in values.items()
        }
    return dict(sorted(finalized.items()))


def _render_contribution_table(
    contributions: dict[str, dict[str, int]],
    *,
    text_mode: bool,
) -> str:
    if text_mode:
        headers = ["Source", "Records", "Restaurants", "Chars", "Price Hits"]
        fields = ["records", "restaurants", "chars", "prices"]
    else:
        headers = [
            "Source",
            "Items",
            "Restaurants",
            "Priced",
            "Dietary Hits",
            "Allergen Hits",
        ]
        fields = [
            "records",
            "restaurants",
            "priced_items",
            "dietary_items",
            "allergen_items",
        ]
    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body = "".join(
        _render_contribution_row(label, values, fields)
        for label, values in sorted(
            contributions.items(),
            key=lambda pair: pair[1].get("records", 0),
            reverse=True,
        )
    )
    return f"<table><thead><tr>{header_html}</tr></thead><tbody>{body}</tbody></table>"


def _render_contribution_row(
    label: str,
    values: dict[str, int],
    fields: list[str],
) -> str:
    cells = [f"<td><span class=\"badge\">{escape(label)}</span></td>"]
    cells.extend(f"<td>{escape(str(values.get(field, 0)))}</td>" for field in fields)
    return f"<tr>{''.join(cells)}</tr>"


def _render_count_row(name: str, count: int) -> str:
    return f"<tr><td>{escape(name or 'n/a')}</td><td>{escape(str(count))}</td></tr>"


def _render_item_row(row: dict[str, str]) -> str:
    signals = "; ".join(
        value
        for value in [row.get("dietary_terms", ""), row.get("allergen_terms", "")]
        if value
    )
    return f"""
      <tr>
        <td>{escape(row.get("restaurant_name", ""))}</td>
        <td><span class="badge">{escape(row.get("extraction_method", "") or "n/a")}</span></td>
        <td><span class="badge">{escape(row.get("source_type", "") or "n/a")}</span><div class="small url">{escape(row.get("menu_source_url", ""))}</div></td>
        <td>{escape(row.get("category", ""))}</td>
        <td>{escape(row.get("item_name", ""))}</td>
        <td>{escape(row.get("price", ""))}</td>
        <td>{escape(signals)}</td>
        <td>{escape(row.get("raw_text", ""))}</td>
      </tr>
"""


def _restaurant_coverage(
    menu_rows: list[dict[str, str]],
    restaurant_rows: list[dict[str, str]],
) -> dict[str, int]:
    restaurants_with_candidates = {
        _restaurant_key(row)
        for row in menu_rows
        if _restaurant_key(row)
    }
    restaurants_with_validated_candidates = {
        _restaurant_key(row)
        for row in menu_rows
        if _restaurant_key(row) and row.get("validation_status") == "validated"
    }

    if restaurant_rows:
        inspected = len({_restaurant_key(row) for row in restaurant_rows if _restaurant_key(row)})
    else:
        inspected = len(restaurants_with_candidates)

    return {
        "restaurants_inspected": inspected,
        "restaurants_with_candidates": len(restaurants_with_candidates),
        "restaurants_with_validated_candidates": len(restaurants_with_validated_candidates),
    }


def _best_menu_rows_by_restaurant(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    best_by_restaurant: dict[str, dict[str, str]] = {}
    for row in rows:
        key = _restaurant_key(row)
        if not key:
            continue
        existing = best_by_restaurant.get(key)
        if existing is None or _menu_row_rank(row) > _menu_row_rank(existing):
            best_by_restaurant[key] = row
    return sorted(
        best_by_restaurant.values(),
        key=lambda row: (row.get("restaurant_name") or "").lower(),
    )


def _menu_row_rank(row: dict[str, str]) -> tuple[int, int, float, int]:
    grade_rank = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}
    status_rank = {"validated": 3, "unvalidated": 2, "not_fetchable": 1}
    type_rank = {
        "schema_org_menu": 6,
        "pdf": 5,
        "nutrition_or_allergen_page": 4,
        "website_link": 3,
        "ordering_page": 2,
        "image": 1,
    }
    try:
        confidence = float(row.get("confidence") or 0)
    except ValueError:
        confidence = 0.0
    return (
        grade_rank.get(row.get("evidence_grade", ""), 0),
        status_rank.get(row.get("validation_status", ""), 0),
        confidence,
        type_rank.get(row.get("source_type", ""), 0),
    )


def _restaurant_key(row: dict[str, str]) -> str:
    return row.get("restaurant_source_id") or row.get("source_id") or row.get("restaurant_name") or row.get("name") or ""


def _inline_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}: {value}" for key, value in counts.items())


def _metric(label: str, value: str) -> str:
    return f'<div class="metric"><strong>{escape(value)}</strong><span>{escape(label)}</span></div>'
