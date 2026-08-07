"""The lookup table: native string -> romanization, and where it came from.

**The table is the product.** The backends are its first-pass filler; a corrected entry
outlives whichever tool produced the first guess, and outlives the tool being swapped.

The rule that makes it improve rather than churn: a row whose ``source`` is ``manual`` is
never overwritten by a **machine guess**. Machine output fills gaps and refreshes machine
rows; hand review is permanent. Without that, every re-run would silently undo the reviewing,
which is the expensive part.

Hand review is not the top of the order, though. An **official gazetteer entry outranks it**:
India Post spells it *Sibsagar* and *Mangaldai* where hand review wrote *Sivasagar* and
*Mangaldoi*, and a published authority beats one person's recollection. The displaced
spelling is not lost -- it moves to ``variant``, because "official" is genuinely not a single
thing here and a reader may want the conventional form. ``authority`` records which source
the value in ``roman`` actually came from.

Precedence, highest first::

    gazetteer  matched against an official list, scoped by pincode or district
    manual     written by hand
    lexicon    every token hand-checked
    indicxlit  model output
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

COLUMNS = ("field", "native", "frequency", "roman", "source", "variant", "authority", "note")

#: A row with this source is hand-reviewed and is never overwritten by a machine guess.
MANUAL = "manual"

#: Sources that are official gazetteers rather than guesses. These outrank hand review.
AUTHORITIES = frozenset({"indiapost", "lgd"})


@dataclass(frozen=True)
class Row:
    field: str
    native: str
    frequency: int
    roman: str = ""
    source: str = ""
    #: The spelling this one displaced, when an authority disagreed with hand review.
    variant: str = ""
    #: Which source ``roman`` came from. Same as ``source`` unless an authority won.
    authority: str = ""
    note: str = ""

    @property
    def key(self) -> Tuple[str, str]:
        return (self.field, self.native)

    @property
    def is_manual(self) -> bool:
        return self.source == MANUAL

    @property
    def is_official(self) -> bool:
        """Whether this row was matched against a published gazetteer."""
        return self.source in AUTHORITIES

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
                variant=r.get("variant", "") or "",
                authority=r.get("authority", "") or "",
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
                        "variant": row.variant,
                        "authority": row.authority,
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
        if prior is not None and (prior.is_manual or prior.is_official):
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
                # ``variant`` and ``authority`` describe the *value*, not the fill that
                # produced this row, so a refill must not reset them. It used to: a row an
                # authority had confirmed but not changed keeps source="lexicon", failed the
                # is_official test above, fell through here, and had its authority rewritten
                # to the source -- silently erasing the confirmation on all 561 such rows.
                variant=prior.variant if prior else "",
                authority=(prior.authority if prior and prior.authority else source),
                note=prior.note if prior else "",
            )
        )
    return out


def adopt(row: Row, official: str, authority: str, keep_variant: bool = True) -> Row:
    """Take an official spelling, keeping the displaced one in ``variant``.

    This is the only path by which hand review is overwritten, and it never destroys: if the
    reviewer wrote *Sivasagar* and India Post says *Sibsagar*, both survive and ``authority``
    says which one ``roman`` now holds.

    ``keep_variant=False`` for rows whose displaced spelling was pure model output. A
    rejected guess is not an alternative spelling, and publishing one as if it were is a
    claim about English usage that nothing supports -- 617 of the first run's 1,186 variants
    were exactly that, 113 of them still carrying the ``x``/``aa`` artifacts this pipeline
    calls wrong. The guess is already in the IndicXlit cache for anyone who wants it.
    """
    if not official or official == row.roman:
        return row
    return replace(
        row,
        roman=official,
        variant=row.roman if keep_variant else "",
        source=authority,
        authority=authority,
    )


def confirm(row: Row, authority: str) -> Row:
    """Record that an official source agreed with the spelling already there.

    Worth storing separately from a correction. "Nobody has checked this" and "India Post
    independently spells it the same way" are very different claims about a row, and only
    one of them is visible if agreement is silent.
    """
    return row if row.authority == authority else replace(row, authority=authority)


def pending(rows: Iterable[Row]) -> List[Row]:
    """Rows still needing a romanization, most frequent first -- the review queue."""
    return sorted((r for r in rows if not r.is_filled), key=lambda r: -r.frequency)
