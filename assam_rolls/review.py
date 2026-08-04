"""HTML review sheet: the page crop beside what was extracted from it.

Reviewing OCR output as a bare CSV is slow and error-prone -- you cannot tell a wrong
value from a correctly-read odd one without the source. This puts the two side by side
so a reviewer can clear or reject a row at a glance, and defaults to showing only rows
that failed a check.

Images are inlined as data URIs so the file is self-contained and can be mailed or
archived alongside the dataset.
"""

from __future__ import annotations

import base64
import html
from pathlib import Path
from typing import Any, Dict, List, Sequence

#: Fields worth eyeballing against the scan, in page order. The full row stays in the CSV.
REVIEW_FIELDS = [
    "ac_no",
    "ac_no_file",
    "part_no",
    "part_no_file",
    "district",
    "ps_no",
    "ps_name",
    "ps_address",
    "pin_code",
    "start_serial",
    "end_serial",
    "electors_male",
    "electors_female",
    "electors_third_gender",
    "electors_total",
    "extraction_confidence",
    "anomaly_notes",
]

STYLE = """
body { font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       margin: 0; padding: 24px; background: #f6f7f9; color: #16191d; }
h1 { font-size: 20px; margin: 0 0 4px; }
.summary { color: #5b6470; margin-bottom: 24px; }
.row { display: grid; grid-template-columns: minmax(320px, 1fr) 1fr; gap: 20px;
       background: #fff; border: 1px solid #dfe3e8; border-radius: 8px;
       padding: 16px; margin-bottom: 20px; }
.row img { width: 100%; border: 1px solid #dfe3e8; border-radius: 4px; }
.key { font-weight: 600; font-size: 15px; margin-bottom: 8px; }
table { border-collapse: collapse; width: 100%; }
td { padding: 4px 8px; border-bottom: 1px solid #eef1f4; vertical-align: top; }
td.label { color: #5b6470; width: 190px; white-space: nowrap; }
td.value { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.flags { margin-top: 10px; padding: 8px 10px; border-radius: 6px;
         background: #fff4f4; border: 1px solid #f3c9c9; color: #8a1f1f;
         font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
.mismatch { background: #fff4f4; }
.ok { color: #1f7a3d; }
"""


def _data_uri(image_path: Path) -> str:
    encoded = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _is_mismatch(row: Dict[str, Any], field: str) -> bool:
    """Highlight the two fields with independent ground truth when they disagree."""
    pairs = {"ac_no": "ac_no_file", "part_no": "part_no_file"}
    counterpart = pairs.get(field)
    if not counterpart:
        return False
    return str(row.get(field, "")).strip() != str(row.get(counterpart, "")).strip()


def _row_html(row: Dict[str, Any], pages_dir: Path) -> str:
    ac_no = str(row.get("ac_no_file") or row.get("ac_no") or "").strip()
    part_no = str(row.get("part_no_file") or row.get("part_no") or "").strip()
    key = f"{int(ac_no):03d}-{int(part_no):04d}" if ac_no.isdigit() and part_no.isdigit() else ""

    image_path = pages_dir / f"{key}.png"
    image_html = (
        f'<img src="{_data_uri(image_path)}" alt="{key}">'
        if image_path.exists()
        else '<div class="flags">page image not found</div>'
    )

    cells = []
    for field in REVIEW_FIELDS:
        css = "value mismatch" if _is_mismatch(row, field) else "value"
        value = html.escape(str(row.get(field, "") or ""))
        cells.append(f'<tr><td class="label">{field}</td><td class="{css}">{value}</td></tr>')

    flags = str(row.get("flags") or "").strip()
    flags_html = (
        f'<div class="flags">{html.escape(flags)}</div>'
        if flags
        else '<div class="key ok">no failed checks</div>'
    )

    heading = f'{html.escape(key)} &middot; {html.escape(str(row.get("source_pdf", "")))}'
    return (
        '<div class="row">'
        f"<div>{image_html}</div>"
        f'<div><div class="key">{heading}</div>'
        f'<table>{"".join(cells)}</table>{flags_html}</div>'
        "</div>"
    )


def build_review_html(
    rows: Sequence[Dict[str, Any]],
    pages_dir: Path,
    only_flagged: bool = True,
    limit: int = 500,
) -> str:
    """Render the review sheet.

    ``limit`` caps how many rows are inlined -- each carries a full-page PNG, so an
    uncapped sheet over a statewide run would be gigabytes. The cap is reported in the
    header rather than applied silently.
    """
    selected: List[Dict[str, Any]] = [
        row
        for row in rows
        if not only_flagged or str(row.get("needs_review", "")).lower() in ("true", "1")
    ]
    shown = selected[:limit]
    truncated = len(selected) - len(shown)

    summary = (
        f"{len(rows)} rows total &middot; {len(selected)} needing review &middot; "
        f"showing {len(shown)}"
    )
    if truncated > 0:
        summary += f" &middot; <strong>{truncated} more not shown (limit {limit})</strong>"

    body = "".join(_row_html(row, pages_dir) for row in shown)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Assam 2026 roll info pages — review</title>"
        f"<style>{STYLE}</style></head><body>"
        "<h1>Assam 2026 roll info pages — review</h1>"
        f'<div class="summary">{summary}</div>'
        f"{body}</body></html>"
    )
