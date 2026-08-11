"""Drop cache entries holding a value the reader could not resolve, so they re-read.

Companion to ``redo_noncanonical.py``. Both exist for the same reason: a fix that only
*adds* readings -- it fires where the previous attempt returned nothing, and never touches
a value that already read -- can be applied to the affected rows alone, without re-running
a corpus that would come out bit-identical.

Selects any part that is not completely clean -- a null text field, an empty area list,
elector counts that do not sum, an anomaly note, or a failed check. Those are exactly the
rows a reader fix could improve; a row with nothing wrong cannot be improved by re-reading
it, and re-reading it would only risk changing something that is already right.

Run, then re-run ``ocr``; it is resumable and refills exactly what was dropped.

    .venv/bin/python scripts/redo_unread.py
    .venv/bin/python -m assam_rolls.cli ocr
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

CACHE = Path("out/cache")

_ELECTORS = ("electors_male", "electors_female", "electors_third_gender", "electors_total")


def _electors_balance(row) -> bool:
    values = [row.get(k) for k in _ELECTORS]
    if any(v is None for v in values):
        return False
    return values[0] + values[1] + values[2] == values[3]


#: Fields read as text. A ``None`` here means ink was present and nothing came back.
TEXT_FIELDS = (
    "ac_name",
    "pc_name",
    "district",
    "block",
    "revenue_circle",
    "police_station",
    "post_office",
    "main_town_village",
    "gram_panchayat",
    "subdivision",
    "ward_no",
    "ps_name",
    "ps_address",
    "roll_description",
    "revision_type",
)


def main() -> int:
    stale, reasons = [], Counter()
    for path in sorted(CACHE.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        row = entry.get("row") or {}
        # A page that failed layout has nothing to re-read here; redo_noncanonical
        # handles those, and re-reading one would only reproduce the same failure.
        if row.get("flags") == "layout_failed":
            continue

        unread = [f for f in TEXT_FIELDS if row.get(f) is None]
        no_sections = not (entry.get("sections") or [])
        unbalanced = not _electors_balance(row)
        noted = bool((row.get("anomaly_notes") or "").strip())
        flagged = bool((row.get("flags") or "").strip())
        if not (unread or no_sections or unbalanced or noted or flagged):
            continue

        for field in unread:
            reasons[field] += 1
        if no_sections:
            reasons["(no sections)"] += 1
        if unbalanced:
            reasons["(electors unbalanced)"] += 1
        if noted:
            reasons["(anomaly note)"] += 1
        if flagged:
            reasons["(check failed)"] += 1
        stale.append(path)

    if not stale:
        print("nothing unread; nothing to do")
        return 0

    print(f"{len(stale):,} parts hold at least one unresolved value\n")
    for name, count in reasons.most_common():
        print(f"   {name:20s} {count:>6,d}")

    for path in stale:
        path.unlink()
    print(f"\ndropped {len(stale):,} cache entries; now re-run:  assam-rolls ocr")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
