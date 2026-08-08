"""Checking the electors against the answer key the source already published.

Most OCR pipelines can only report how confident they feel. This one does not have to. Every
part's info page states how many electors it contains, split by sex, and that arithmetic was
verified across all 31,486 parts when ``dataset/parts.jsonl.gz`` was built. So each part
arrives with a published count of what this stage should produce.

That gives three independent checks, in descending order of how much they prove:

``count``   extracted rows == the printed total. The strongest: it catches a page that
            failed to resolve, a box read as empty, and a part processed twice.
``sex``     the male/female split matches. Catches systematic misreading of one sex.
``serial``  serials run 1..N with no gaps. Catches an ordering or numbering fault even
            where the count happens to come out right.

Field-level checks follow the shape used in ``parse_unsearchable_rolls`` -- types, ranges and
field sizes -- but they are secondary. They say a value is *plausible*; only the counts say
the extraction is *complete*.
"""

from __future__ import annotations

import gzip
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

PARTS = Path("dataset/parts.jsonl.gz")

EPIC_RE = re.compile(r"^[A-Z]{2,4}\d{6,9}$")

MIN_AGE, MAX_AGE = 18, 120
MIN_NAME, MAX_NAME = 2, 80


@dataclass
class PartCheck:
    """One part, measured against what its info page says it contains."""

    ac_no: int
    part_no: int
    expected: Optional[int]
    extracted: int
    expected_male: Optional[int] = None
    expected_female: Optional[int] = None
    male: int = 0
    female: int = 0
    serial_gaps: int = 0

    @property
    def counts_match(self) -> bool:
        return self.expected is not None and self.extracted == self.expected

    @property
    def shortfall(self) -> Optional[int]:
        return None if self.expected is None else self.extracted - self.expected


def load_part_totals(path: Path = PARTS) -> Dict[tuple, Dict[str, Any]]:
    """``(ac_no, part_no)`` -> the published elector counts."""
    out: Dict[tuple, Dict[str, Any]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            out[(row["ac_no"], row["part_no"])] = {
                "total": row.get("electors_total"),
                "male": row.get("electors_male"),
                "female": row.get("electors_female"),
                "third": row.get("electors_third_gender"),
            }
    return out


def reconcile(
    rows: Sequence[Dict[str, Any]], totals: Dict[tuple, Dict[str, Any]]
) -> List[PartCheck]:
    """Compare what was extracted with what each part's info page published."""
    by_part: Dict[tuple, List[Dict[str, Any]]] = {}
    for row in rows:
        by_part.setdefault((row["ac_no"], row["part_no"]), []).append(row)

    checks: List[PartCheck] = []
    for key, part_rows in sorted(by_part.items()):
        published = totals.get(key, {})
        sexes = Counter(r.get("sex") for r in part_rows)
        serials = sorted(r["serial_no"] for r in part_rows if r.get("serial_no"))
        gaps = sum(1 for a, b in zip(serials, serials[1:]) if b - a != 1) + (
            1 if serials and serials[0] != 1 else 0
        )
        checks.append(
            PartCheck(
                ac_no=key[0],
                part_no=key[1],
                expected=published.get("total"),
                extracted=len(part_rows),
                expected_male=published.get("male"),
                expected_female=published.get("female"),
                male=sexes.get("M", 0),
                female=sexes.get("F", 0),
                serial_gaps=gaps,
            )
        )
    return checks


def field_report(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Fill rates and plausibility, per field."""
    rows = list(rows)
    total = len(rows) or 1
    epic = [r.get("epic_no") or "" for r in rows]
    names = [r.get("name") or "" for r in rows]
    ages = [r.get("age") for r in rows]
    return {
        "rows": len(rows),
        "epic_present": sum(1 for e in epic if e) / total,
        "epic_well_formed": sum(1 for e in epic if EPIC_RE.match(e)) / total,
        "epic_unique": len({e for e in epic if e}) / max(1, sum(1 for e in epic if e)),
        "name_present": sum(1 for n in names if n) / total,
        "name_plausible": sum(1 for n in names if MIN_NAME <= len(n) <= MAX_NAME) / total,
        "relation_present": sum(1 for r in rows if r.get("relation_name")) / total,
        "house_present": sum(1 for r in rows if r.get("house_no")) / total,
        "age_present": sum(1 for a in ages if a) / total,
        "age_plausible": sum(1 for a in ages if a and MIN_AGE <= a <= MAX_AGE) / total,
        "sex_present": sum(1 for r in rows if r.get("sex")) / total,
        "needs_review": sum(1 for r in rows if r.get("needs_review")) / total,
        "flags": dict(Counter(f for r in rows for f in (r.get("flags") or "").split(",") if f)),
    }


def summarize(checks: Sequence[PartCheck], rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The numbers a report should quote, with the count reconciliation first."""
    checkable = [c for c in checks if c.expected is not None]
    exact = [c for c in checkable if c.counts_match]
    return {
        "parts": len(checks),
        "parts_with_published_total": len(checkable),
        "parts_exact": len(exact),
        "parts_exact_rate": len(exact) / max(1, len(checkable)),
        "electors_extracted": sum(c.extracted for c in checks),
        "electors_expected": sum(c.expected or 0 for c in checkable),
        "parts_with_serial_gaps": sum(1 for c in checks if c.serial_gaps),
        "worst_shortfalls": [
            {"ac_no": c.ac_no, "part_no": c.part_no, "expected": c.expected, "got": c.extracted}
            for c in sorted(checkable, key=lambda c: c.shortfall or 0)[:10]
        ],
        "fields": field_report(rows),
    }
