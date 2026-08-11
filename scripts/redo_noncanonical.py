"""Drop the pages the publisher issued at a non-canonical size, so they re-render.

ACs are the wrong unit here: the size varies **per page**, and 19 of the 28 affected
constituencies hold a mix (AC82 has one odd page in 181, AC73 has 161 in 206). So the set
is keyed off the rendered pages themselves rather than a list of constituencies.

Run after an OCR pass, then re-run ``render`` and ``ocr``: both are resumable, so they
re-do exactly what was deleted and nothing else.

    .venv/bin/python scripts/redo_noncanonical.py
    .venv/bin/python -m assam_rolls.cli render
    .venv/bin/python -m assam_rolls.cli ocr
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assam_rolls.render import CANONICAL_PAGE_SIZE  # noqa: E402

PAGES = Path("out/pages")
CACHE = Path("out/cache")


def main() -> int:
    stale = []
    for path in sorted(PAGES.glob("*.png")):
        with Image.open(path) as image:
            if image.size != CANONICAL_PAGE_SIZE:
                stale.append(path)

    if not stale:
        print("no non-canonical pages; nothing to do")
        return 0

    print(f"{len(stale):,} pages are not {CANONICAL_PAGE_SIZE[0]}x{CANONICAL_PAGE_SIZE[1]}")
    acs = sorted({int(p.stem.split("-")[0]) for p in stale})
    print(f"across {len(acs)} constituencies: {acs}")

    dropped_cache = 0
    for path in stale:
        entry = CACHE / f"{path.stem}.json"
        if entry.exists():
            entry.unlink()
            dropped_cache += 1
        path.unlink()

    print(f"\ndeleted {len(stale):,} page images and {dropped_cache:,} cache entries")
    print("now re-run:  assam-rolls render  &&  assam-rolls ocr")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
