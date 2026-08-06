"""The lookup table: native string -> romanization, and where it came from.

**The table is the product.** The backends are its first-pass filler; a corrected entry
outlives whichever tool produced the first guess, and outlives the tool being swapped.

The rule that makes it improve rather than churn: a row whose ``source`` is ``manual`` is
never overwritten by a re-run. Machine output fills gaps and refreshes machine rows; hand
review is permanent. Without that, every re-run would silently undo the reviewing, which is
the expensive part.
"""

from __future__ import annotations

import csv
import gzip
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

TABLE = Path("dataset/transliteration.csv.gz")

COLUMNS = ("field", "native", "frequency", "roman", "source", "note")

#: A row with this source is hand-reviewed and is never machine-overwritten.
MANUAL = "manual"


@dataclass(frozen=True)
class Row:
    field: str
    native: str
    frequency: int
    roman: str = ""
    source: str = ""
    note: str = ""

    @property
    def key(self) -> Tuple[str, str]:
        return (self.field, self.native)

    @property
    def is_manual(self) -> bool:
        return self.source == MANUAL

    @property
    def is_filled(self) -> bool:
        return bool(self.roman)


def read(path: Path = TABLE) -> Dict[Tuple[str, str], Row]:
    if not path.exists():
        return {}
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        rows = [
            Row(
                field=r["field"],
                native=r["native"],
                frequency=int(r["frequency"] or 0),
                roman=r.get("roman", ""),
                source=r.get("source", ""),
                note=r.get("note", ""),
            )
            for r in csv.DictReader(handle)
        ]
    return {row.key: row for row in rows}


def write(rows: Iterable[Row], path: Path = TABLE) -> int:
    """Write the table atomically, so an interrupted run cannot truncate it.

    Same reasoning as the extraction cache: this file accumulates hand review, and a
    half-written version that still parses would be worse than no version at all.
    """
    rows = sorted(rows, key=lambda r: (r.field, -r.frequency, r.native))
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "wb", dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp", delete=False
    )
    try:
        with gzip.open(handle, "wt", encoding="utf-8", newline="") as gz:
            writer = csv.DictWriter(gz, fieldnames=COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        "field": row.field,
                        "native": row.native,
                        "frequency": row.frequency,
                        "roman": row.roman,
                        "source": row.source,
                        "note": row.note,
                    }
                )
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(handle.name, path)
    except BaseException:
        handle.close()
        Path(handle.name).unlink(missing_ok=True)
        raise
    return len(rows)


def merge(
    existing: Dict[Tuple[str, str], Row],
    entries: Iterable,
    filled: Optional[Dict[Tuple[str, str], Tuple[str, str]]] = None,
) -> List[Row]:
    """Fold new vocabulary and fresh machine output into the table.

    ``entries`` are ``vocabulary.Entry``; ``filled`` maps a key to ``(roman, source)``.

    Manual rows keep their romanization and only have their frequency refreshed -- the
    frequency is a property of the corpus, the romanization is a property of the review.
    """
    filled = filled or {}
    out: List[Row] = []
    for entry in entries:
        key = entry.key
        prior = existing.get(key)
        if prior is not None and prior.is_manual:
            out.append(replace(prior, frequency=entry.frequency))
            continue
        roman, source = filled.get(key, ("", ""))
        if not roman and prior is not None:
            roman, source = prior.roman, prior.source
        out.append(
            Row(
                field=entry.field,
                native=entry.native,
                frequency=entry.frequency,
                roman=roman,
                source=source,
                note=prior.note if prior else "",
            )
        )
    return out


def pending(rows: Iterable[Row]) -> List[Row]:
    """Rows still needing a romanization, most frequent first -- the review queue."""
    return sorted((r for r in rows if not r.is_filled), key=lambda r: -r.frequency)
