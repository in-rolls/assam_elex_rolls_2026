"""Write the dataset as JSONL and CSV.

**JSONL is authoritative.** It preserves what the data actually is: integers stay
integers, ``null`` stays distinct from ``""``, Assamese stays UTF-8 without escaping, and
each part carries its own section list rather than pointing at a second table.

CSV is written for accessibility, and gives two things up in the process:

* it cannot represent null, so ``null`` becomes ``NA`` and a genuinely blank field
  becomes empty. ``NA`` is R's native missing marker and pandas reads it via
  ``na_values=["NA"]``;
* it is flat, so sections move to their own file joined on ``(ac_no_file, part_no_file)``.

It is written **utf-8-sig**. Excel decodes a UTF-8 CSV as the system codepage unless it
finds a BOM, which turns every Assamese field into mojibake -- and a roll dataset is
opened in Excel. Python, R and pandas all strip the BOM transparently.

Null convention, decided in ``parse``:

``null`` / ``NA``
    the pipeline could not read a value that appears to be there.
``""``
    the form prints nothing there -- rural parts have no ward number.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .log import get_logger
from .schema import OUTPUT_COLUMNS, SECTION_COLUMNS

logger = get_logger(__name__)

#: What CSV writes where JSON has null. Empty string stays empty, preserving the
#: distinction as far as CSV allows.
CSV_NULL = "NA"

#: Excel needs the BOM to detect UTF-8; every other reader ignores it.
CSV_ENCODING = "utf-8-sig"


def _csv_value(value: Any) -> str:
    if value is None:
        return CSV_NULL
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _sections_for(part: Dict[str, Any], sections: Sequence[Dict[str, Any]]):
    ac, part_no = part.get("ac_no_file"), part.get("part_no_file")
    return [s for s in sections if s.get("ac_no") == ac and s.get("part_no") == part_no]


def write_jsonl(
    path: Path,
    parts: Sequence[Dict[str, Any]],
    sections: Sequence[Dict[str, Any]] = (),
    columns: Optional[Sequence[str]] = None,
) -> int:
    """Write one self-contained JSON object per part, sections nested inside.

    ``ensure_ascii=False`` keeps Assamese as Assamese rather than ``\\uXXXX`` escapes:
    the file stays readable and roughly a third the size.
    """
    columns = list(columns or OUTPUT_COLUMNS)
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("w", encoding="utf-8") as handle:
        for part in parts:
            record = {column: part.get(column) for column in columns}
            record["sections"] = [
                {
                    key: section.get(key)
                    for key in SECTION_COLUMNS
                    if key not in ("ac_no", "part_no")
                }
                for section in _sections_for(part, sections)
            ]
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
    logger.info("wrote %s (%d records)", path, written)
    return written


def write_csv(
    path: Path,
    rows: Sequence[Dict[str, Any]],
    columns: Sequence[str],
) -> int:
    """Write a flat CSV, BOM-prefixed so Excel renders Assamese."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding=CSV_ENCODING) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_value(row.get(column)) for column in columns})
    logger.info("wrote %s (%d rows)", path, len(rows))
    return len(rows)


def write_dataset(
    out_dir: Path,
    parts: Sequence[Dict[str, Any]],
    sections: Sequence[Dict[str, Any]],
    columns: Optional[Sequence[str]] = None,
) -> Dict[str, int]:
    """Write the full dataset in both formats."""
    columns = list(columns or OUTPUT_COLUMNS)
    return {
        "parts.jsonl": write_jsonl(out_dir / "parts.jsonl", parts, sections, columns),
        "parts.csv": write_csv(out_dir / "parts.csv", parts, columns),
        "part_sections.csv": write_csv(out_dir / "part_sections.csv", sections, SECTION_COLUMNS),
    }


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Read back a JSONL file, for verification and downstream use."""
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def flatten(records: Iterable[Dict[str, Any]]):
    """Split nested JSONL records back into (parts, sections)."""
    parts, sections = [], []
    for record in records:
        part = {k: v for k, v in record.items() if k != "sections"}
        parts.append(part)
        for section in record.get("sections") or []:
            sections.append(
                {"ac_no": part.get("ac_no_file"), "part_no": part.get("part_no_file"), **section}
            )
    return parts, sections
