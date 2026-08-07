"""The distinct strings worth transliterating, with how often each occurs.

The corpus has **low uniqueness**: 31,486 parts collapse to 41 distinct districts, 267
revenue circles, 366 police stations, 723 blocks and 1,420 post offices. So the unit of
work is the distinct string, not the row -- transliterating 2,817 strings covers every row
in the dataset, and joining the result back on is free.

That ratio is what makes a hand-reviewable lookup table possible at all. 41 district
strings cover **100%** of rows.
"""

from __future__ import annotations

import gzip
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List

DATASET = Path("dataset/parts.jsonl.gz")

#: Fields transliterated now. Ordered by how closed the set is: districts are a fixed
#: administrative list, station names are effectively unique per row.
TIER1_FIELDS = (
    "district",
    "revenue_circle",
    "police_station",
    "block",
    "post_office",
)

#: Far more volume -- 12,459 and 31,368 distinct -- but not proportionally more work. A
#: station name is a place name plus a school, and the school half is a closed vocabulary:
#: ``স্কুল`` occurs in 19,600 rows, ``বিদ্যালয়`` in 7,489. 200 tokens cover half of it.
TIER2_FIELDS = ("main_town_village", "ps_name")

#: Everything the table covers.
ALL_FIELDS = TIER1_FIELDS + TIER2_FIELDS


@dataclass(frozen=True)
class Entry:
    """One distinct native string in one field."""

    field: str
    native: str
    frequency: int
    lang: str

    @property
    def key(self) -> tuple:
        return (self.field, self.native)


def load_rows(path: Path = DATASET) -> List[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def build(rows: Iterable[dict], fields: Iterable[str] = TIER1_FIELDS) -> List[Entry]:
    """Distinct (field, native) pairs with frequencies, most common first.

    The language is carried through because Assamese and Bengali need different
    transliteration settings -- they share a script but not a letter inventory.
    """
    counts: Dict[tuple, int] = Counter()
    langs: Dict[tuple, Counter] = {}
    for row in rows:
        for field in fields:
            value = row.get(field)
            if not value:
                continue
            key = (field, value)
            counts[key] += 1
            langs.setdefault(key, Counter())[row.get("lang") or "ASM"] += 1

    entries = [
        Entry(
            field=field,
            native=native,
            frequency=n,
            lang=langs[(field, native)].most_common(1)[0][0],
        )
        for (field, native), n in counts.items()
    ]
    entries.sort(key=lambda e: (e.field, -e.frequency, e.native))
    return entries


def summarize(entries: Iterable[Entry]) -> Dict[str, Dict[str, int]]:
    """Per-field distinct count and row coverage, for the audit header."""
    out: Dict[str, Dict[str, int]] = {}
    for entry in entries:
        bucket = out.setdefault(entry.field, {"distinct": 0, "rows": 0})
        bucket["distinct"] += 1
        bucket["rows"] += entry.frequency
    return out
