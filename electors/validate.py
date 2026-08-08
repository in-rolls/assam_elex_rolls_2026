"""Checking the electors against the answer key the source already published.

Most OCR pipelines can only report how confident they feel. This one does not have to. Every
part's info page states how many electors it contains, split by sex, and that arithmetic was
verified across all 31,486 parts when ``dataset/parts.jsonl.gz`` was built. So each part
arrives with a published count of what this stage should produce.

**But that figure is a net, and the printed roll does not equal it.** Part 1 prints 870
entries against a published 850, part 9 prints 810 against 791 -- with no supplement pages, no
blank boxes, and no deletion marking anywhere in the ink (min 0.086, median 0.108, max 0.138:
one tight population). The gap is in the publisher's arithmetic -- deletions counted but not
reprinted -- and is not recoverable from these PDFs. Treating "extracted == published" as the
target would mean deleting real rows until a number that cannot be right looks right.

So the checks are separated by what each can actually prove:

``serial``     serials run 1..N within each list, no gaps. Ordering and numbering are
               intact. This is checkable from the data alone.
``sex``        the male/female split against the published one. A *ratio* check: it caught a
               matcher that returned 41/59 against a published 51/49, which no count would
               have shown.
``published``  printed entries minus the published net, reported **as a difference with an
               outlier band** rather than as pass/fail. A part diverging far more than its
               neighbours is worth looking at; a part diverging like its neighbours is the
               publisher's arithmetic, not a defect.

Field-level checks follow the shape used in ``parse_unsearchable_rolls`` -- types, ranges and
field sizes. They say a value is *plausible*; nothing here says the extraction is complete,
because the source does not publish a number that would establish it.
"""

from __future__ import annotations

import gzip
import json
import re
import statistics
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
    supplement_rows: int = 0

    @property
    def counts_match(self) -> bool:
        return self.expected is not None and self.extracted == self.expected

    @property
    def shortfall(self) -> Optional[int]:
        return None if self.expected is None else self.extracted - self.expected

    @property
    def male_share(self) -> Optional[float]:
        sexed = self.male + self.female
        return (self.male / sexed) if sexed else None

    @property
    def expected_male_share(self) -> Optional[float]:
        if self.expected_male is None or self.expected_female is None:
            return None
        total = self.expected_male + self.expected_female
        return (self.expected_male / total) if total else None


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
        # Serials restart per list, so gaps are counted within a list rather than across the
        # part -- otherwise every supplement would register as one large false gap.
        gaps = 0
        for section in {r.get("roll_section") or "main" for r in part_rows}:
            serials = sorted(
                r["serial_no"]
                for r in part_rows
                if r.get("serial_no") and (r.get("roll_section") or "main") == section
            )
            gaps += sum(1 for a, b in zip(serials, serials[1:]) if b - a != 1)
            gaps += 1 if serials and serials[0] != 1 else 0
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
                supplement_rows=sum(
                    1 for r in part_rows if (r.get("roll_section") or "main") != "main"
                ),
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


#: A part whose printed-minus-published gap is this many standard deviations from the
#: corpus norm is an outlier worth a look. Within the band it is the publisher's arithmetic.
OUTLIER_SIGMA = 3.0


def summarize(checks: Sequence[PartCheck], rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The numbers a report should quote, each labelled by what it can prove."""
    checkable = [c for c in checks if c.expected is not None]
    gaps = [c.shortfall for c in checkable if c.shortfall is not None]
    mean = statistics.fmean(gaps) if gaps else 0.0
    sigma = statistics.pstdev(gaps) if len(gaps) > 1 else 0.0

    outliers = [
        {
            "ac_no": c.ac_no,
            "part_no": c.part_no,
            "printed": c.extracted,
            "published": c.expected,
            "gap": c.shortfall,
        }
        for c in checkable
        if sigma > 0 and abs((c.shortfall or 0) - mean) > OUTLIER_SIGMA * sigma
    ]

    shares = [
        (c.male_share, c.expected_male_share)
        for c in checks
        if c.male_share is not None and c.expected_male_share is not None
    ]
    return {
        "parts": len(checks),
        "rows": sum(c.extracted for c in checks),
        "supplement_rows": sum(c.supplement_rows for c in checks),
        # Checkable from the data alone: numbering is intact.
        "parts_with_serial_gaps": sum(1 for c in checks if c.serial_gaps),
        # A ratio, not a count -- this is what catches a biased field.
        "male_share": statistics.fmean(a for a, _ in shares) if shares else None,
        "published_male_share": statistics.fmean(b for _, b in shares) if shares else None,
        # The publisher's net differs from what it prints; reported, not treated as a target.
        "printed_minus_published_mean": round(mean, 1),
        "printed_minus_published_sd": round(sigma, 1),
        "gap_outliers": outliers,
        "fields": field_report(rows),
    }
