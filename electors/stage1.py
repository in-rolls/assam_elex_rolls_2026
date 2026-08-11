"""Stage one: everything the CPU can do, and nothing that costs money.

The pipeline splits at the only expensive step. This stage renders each part, finds the boxes
geometrically from the ruled lines, finds the text lines inside them, reads the three fields
tesseract is good enough for -- the EPIC, the section header and the closing totals -- and packs
the four labelled lines into composites for Cloud Vision.

Everything it produces is what stage two needs and nothing more:

- **composites**, the images Vision is billed for. Repacking cuts a rendered page from 15.5 MP
  to 30% of that, which is 3.3x fewer images: $345 for the state against about $105.
- **placements**, every tile's rectangle inside its composite. Vision answers in the composite's
  coordinates and a word has to come back to the box it was cut from; without this, stage two
  would have to re-derive the geometry and could silently disagree with the geometry that
  produced the image.
- **a manifest**, one row per tile carrying ac, part, page, box, band, rectangle, section and
  the source sha, so a reading can be put in the right row and traced to exact source bytes.
- **side reads**, the EPIC and serial per box and the part's printed closing total, which is the
  only number in the source that the extracted rows can be measured against.

It costs about 142 ms of CPU per box -- 985 core-hours for all 126 constituencies, roughly $8 of
spot CPU or five days on a laptop -- and nothing at all in API charges.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from assam_rolls import render, schema

from . import crops, extract, pages, repack, vision, vision_part


@dataclass
class PartPrep:
    """What stage one produced for one part, and what it could not."""

    ac_no: int
    part_no: int
    pages: int = 0
    elector_pages: int = 0
    boxes: int = 0
    main_boxes: int = 0
    supplement_boxes: int = 0
    composites: int = 0
    unknown_pages: List[int] = field(default_factory=list)
    summary_total: Optional[int] = None
    seconds: float = 0.0
    error: str = ""

    @property
    def matches_roll(self) -> Optional[bool]:
        """Whether the **main-list** boxes equal the part's own printed total.

        Main list only. The closing page totals ``মূল তালিকা`` -- the main roll -- and a part's
        supplements are counted separately, so comparing every box against it makes a part with
        supplements look over-extracted. Part 4 read 511 boxes against a printed 483 and the 28
        were its supplement, correctly found.

        ``None`` when the closing page could not be read -- reported as unmeasured rather than
        scored against a number that was never established.
        """
        if self.summary_total is None:
            return None
        return self.main_boxes == self.summary_total


def prepare_part(
    zip_path: Path,
    pdf_name: str,
    part_no: int,
    out_dir: Path,
    dpi: int = extract.DPI,
) -> PartPrep:
    """Render one part and write everything stage two will need for it."""
    started = time.time()
    meta = schema.parse_source_filename(pdf_name) or {}
    prep = PartPrep(ac_no=meta.get("ac_no", 0), part_no=part_no)
    part_dir = out_dir / f"part{part_no:04d}"

    try:
        import tempfile

        pdf_bytes = render.read_pdf_bytes(zip_path, pdf_name)
        sha = render.sha256_bytes(pdf_bytes)
        with tempfile.TemporaryDirectory() as tmp:
            images = vision_part.rasterize(dpi)(pdf_bytes, Path(tmp))
            prep.pages = len(images)
            entries, side = vision_part.tiles_for(images)
            prep.unknown_pages = side["unknown"]
            prep.elector_pages = len(side["pages"])
            prep.boxes = len(entries)
            for _, _, key in entries:
                if side["pages"][key[0]]["section"] is pages.Section.MAIN:
                    prep.main_boxes += 1
                else:
                    prep.supplement_boxes += 1
            if side["summary"] is not None:
                prep.summary_total = side["summary"].total

            part_dir.mkdir(parents=True, exist_ok=True)
            budget = int(vision.MAX_PIXELS * vision.PIXEL_MARGIN)
            placements: Dict[str, Any] = {}
            written: List[crops.Crop] = []
            for number, batch in enumerate(repack.batches(entries, budget)):
                composite, placed = repack.compose(batch)
                # Keyed by the filename itself, not a stem the reader has to know how to
                # complete. The first version keyed on "composite000" and the verifier went
                # looking for a file of that name.
                name = f"composite{number:03d}.png"
                composite.save(part_dir / name, format="PNG", optimize=True)
                placements[name] = [
                    {
                        "page": tile.key[0],
                        "box_row": tile.key[1],
                        "box_col": tile.key[2],
                        "left": tile.left,
                        "top": tile.top,
                        "right": tile.right,
                        "bottom": tile.bottom,
                    }
                    for tile in placed
                ]
                prep.composites += 1
                for _, rect, key in batch:
                    page_side = side["pages"][key[0]]
                    written.append(
                        crops.Crop(
                            part=part_no,
                            page=key[0],
                            row=key[1],
                            column=key[2],
                            path=part_dir / name,
                            band=None,
                            left=rect[0],
                            top=rect[1],
                            right=rect[2],
                            bottom=rect[3],
                            ac_no=prep.ac_no,
                            section=page_side["section"].value,
                            source_pdf=pdf_name,
                            pdf_sha256=sha,
                        )
                    )

            _write(part_dir / "placements.json", placements)
            _write(
                part_dir / "side.json",
                {
                    "epic": {
                        f"{k[0]},{k[1]},{k[2]}": text
                        for page in side["pages"].values()
                        for k, text in page["boxes"].items()
                    },
                    "sections": {str(n): p["section"].value for n, p in side["pages"].items()},
                    "unrecognised_headers": [
                        n for n, p in side["pages"].items() if not p["recognised"]
                    ],
                    "summary_total": prep.summary_total,
                },
            )
            crops.write_manifest(written, part_dir, append=False)
            for image in images:
                image.close()
    except Exception as exc:  # a bad part must not stop the constituency
        prep.error = f"{type(exc).__name__}: {exc}"
    prep.seconds = time.time() - started
    return prep


def _write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def done(out_dir: Path, part_no: int) -> bool:
    """Whether this part is already prepared, so a run can be resumed.

    Requires the manifest *and* the placements: a part interrupted between writing composites
    and writing where the tiles are cannot be read by stage two, and half-finished work that
    looks finished is worse than work that was never started.
    """
    part_dir = out_dir / f"part{part_no:04d}"
    return (part_dir / crops.MANIFEST).exists() and (part_dir / "placements.json").exists()


def summarise(preps: Sequence[PartPrep]) -> Dict[str, Any]:
    """What the run established, in the terms the next stage and the bill are both in."""
    ok = [p for p in preps if not p.error]
    measured = [p for p in ok if p.matches_roll is not None]
    return {
        "parts": len(preps),
        "failed": len(preps) - len(ok),
        "pages": sum(p.pages for p in ok),
        "boxes": sum(p.boxes for p in ok),
        "main_boxes": sum(p.main_boxes for p in ok),
        "supplement_boxes": sum(p.supplement_boxes for p in ok),
        "composites": sum(p.composites for p in ok),
        "parts_measured": len(measured),
        "parts_matching_roll": sum(1 for p in measured if p.matches_roll),
        "parts_unmeasured": [p.part_no for p in ok if p.matches_roll is None],
        "residuals": [
            {
                "part_no": p.part_no,
                "main_boxes": p.main_boxes,
                "supplement_boxes": p.supplement_boxes,
                "roll_total": p.summary_total,
                "diff": p.main_boxes - (p.summary_total or 0),
            }
            for p in measured
            if not p.matches_roll
        ],
        "unknown_pages": {p.part_no: p.unknown_pages for p in ok if p.unknown_pages},
        "seconds": sum(p.seconds for p in preps),
        "vision_cost": vision.cost(sum(p.composites for p in ok)),
    }
