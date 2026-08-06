"""Drop cache entries holding a value the reader could not resolve, so they re-read.

Companion to ``redo_noncanonical.py``. Both exist for the same reason: a fix that only
*adds* readings -- it fires where the previous attempt returned nothing, and never touches
a value that already read -- can be applied to the affected rows alone, without re-running
a corpus that would come out bit-identical.

Selects a part when, on a page whose layout was read successfully, either:

* a text field is ``None`` -- ink is present and nothing was recognised; or
* the numbered-area list is empty, which no page in this corpus actually is.

Run, then re-run ``ocr``; it is resumable and refills exactly what was dropped.

    .venv/bin/python scripts/redo_unread.py
    .venv/bin/python -m assam_rolls.cli ocr
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

CACHE = Path("out/cache")

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
        if not unread and not no_sections:
            continue

        for field in unread:
            reasons[field] += 1
        if no_sections:
            reasons["(no sections)"] += 1
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
