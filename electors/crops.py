"""Cutting box images out for a reader that runs somewhere else.

Cloud Vision reads the state, and once it has, **nothing checks it**. There are no labels at
this scale, so the only automatic error signal is two independent engines disagreeing --
dots.ocr is the strongest second reader available and it fails differently, being pure OCR with
no language prior where Vision has one.

It cannot run the state: it reads per box, so 25 million inferences, which is months of free
GPU quota for accuracy it already ties. On a sample it is minutes, and a sample is all a check
needs. That means getting a few thousand box images to a machine with a GPU, which is what this
module is for.

**The filename is the key.** ``p13_pg4_r2c0.png`` is part 13, page 4, box row 2, column 0 --
the same spelling ``dataset/eval`` uses and the same thing :data:`electors.resolution.KEY`
parses. So a folder of crops and a JSON of readings need nothing else to be lined up against
what Vision said about the same boxes; there is no separate manifest to fall out of step.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple

from PIL import Image

from assam_rolls import render

from . import extract, grid, pages

#: ``p13_pg4_r2c0`` -- the same spelling ``dataset/eval`` and :mod:`electors.resolution` use.
NAME = re.compile(r"p(\d+)_pg(\d+)_r(\d+)c(\d+)")


def name_for(part: int, page: int, row: int, column: int) -> str:
    return f"p{part}_pg{page}_r{row}c{column}"


def key_of(name: str) -> Optional[Tuple[int, int, int, int]]:
    """``(part, page, row, column)`` from a crop's filename, or None."""
    found = NAME.match(Path(name).stem)
    return tuple(int(g) for g in found.groups()) if found else None  # type: ignore[return-value]


@dataclass
class Crop:
    """One box image and where it came from."""

    part: int
    page: int
    row: int
    column: int
    path: Path

    @property
    def key(self) -> Tuple[int, int, int, int]:
        return (self.part, self.page, self.row, self.column)


def boxes_of(image: Image.Image, number: int) -> List[grid.Box]:
    """The elector boxes on one page, or nothing if it is not an elector page."""
    signature = pages.classify(image, number)
    if not signature.is_elector:
        return []
    return grid.build(signature.h_rules, signature.v_rules)


def export_part(
    zip_path: Path,
    pdf_name: str,
    part_no: int,
    out_dir: Path,
    dpi: int = extract.DPI,
    limit: Optional[int] = None,
) -> List[Crop]:
    """Write every readable box of one part as a PNG named after its position.

    Cropped to ``text_right`` rather than the box's full width: everything past it is the photo
    placeholder, and it is the same region the pipeline reads. Sending the photo would change
    what the second reader is being asked, which is the one thing a comparison cannot afford.

    Boxes with no ink are skipped before anything is written -- a blank box at the end of a part
    is the publisher's doing and there is nothing in it to read.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_bytes = render.read_pdf_bytes(zip_path, pdf_name)
    written: List[Crop] = []

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for number, path in enumerate(extract._render_pages(pdf_bytes, workdir, dpi=dpi), start=1):
            with Image.open(path) as page_image:
                image = page_image.convert("L")
                for box in boxes_of(image, number):
                    if limit is not None and len(written) >= limit:
                        return written
                    if not grid.has_ink(image, box):
                        continue
                    crop = image.crop((box.left, box.top, box.text_right, box.bottom))
                    name = name_for(part_no, number, box.row, box.column)
                    target = out_dir / f"{name}.png"
                    crop.save(target, format="PNG", optimize=True)
                    written.append(Crop(part_no, number, box.row, box.column, target))
    return written


def export(
    zip_path: Path,
    parts: Sequence,
    out_dir: Path,
    dpi: int = extract.DPI,
    limit: Optional[int] = None,
) -> Iterator[Tuple[int, List[Crop]]]:
    """Every part in turn, yielding as each finishes so a long export shows progress."""
    total = 0
    for part in parts:
        remaining = None if limit is None else max(0, limit - total)
        if remaining == 0:
            return
        found = export_part(
            zip_path, part.pdf_name, part.part_no, out_dir, dpi=dpi, limit=remaining
        )
        total += len(found)
        yield part.part_no, found


def readings_to_arm(readings: dict, arm: str = "dots.ocr") -> List:
    """Turn ``{crop name: terse text}`` into the arms :mod:`electors.resolution` compares.

    The readings come back from whatever GPU ran them as one JSON object. Parsed here with the
    *same* parser Vision's output goes through, because two readers of one format is the bug
    that put the elector's own name in the relation field, and comparing engines through two
    different parsers would measure the parsers.
    """
    from . import resolution, second_pass

    by_part: dict = {}
    for name, text in readings.items():
        key = key_of(name)
        if not key:
            continue
        part, page, row, column = key
        found = second_pass.Reading(str(text or "")).fields()
        by_part.setdefault(part, {})[(page, row, column)] = {
            "name": found.get("name", ""),
            "age": found.get("age"),
            "house": found.get("house_no", ""),
            "sex": found.get("sex", ""),
        }
    return [
        resolution.ArmResult(arm=arm, part=part, boxes=boxes)
        for part, boxes in sorted(by_part.items())
    ]


def zip_dir(directory: Path, target: Path) -> Path:
    """Zip a crop folder for upload. Kaggle takes a dataset far more happily than 10,000 files."""
    subprocess.run(
        ["zip", "-q", "-r", "-0", str(target), "."],
        cwd=directory,
        check=True,
        capture_output=True,
    )
    return target
