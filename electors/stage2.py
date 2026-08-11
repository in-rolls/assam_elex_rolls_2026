"""Stage two: the only step that costs money, and nothing else.

Stage one already rendered the part, found the boxes, read the EPICs and packed the text into
composites. This stage submits those composites to Cloud Vision and puts the words back in the
rows they came from. It never opens the source PDF.

That is the whole point of the split. ``vision_part.read_repacked`` does both halves in one pass,
which means re-running Vision -- after a parser change, or with a second engine -- re-renders 121
pages of PDF and re-runs tesseract over every box to get back to pixels that are already sitting
on disk. Here the composites are the input, so a second reading costs the API call and nothing
else.

The join is the load-bearing part. Vision answers in each **composite's** coordinates, and a word
in tile 200 has to come back to box 200 of page 14. ``placements.json`` holds every tile's
rectangle and ``repack.words_for`` assigns by that rectangle on both axes, so two tiles side by
side never share a word even when Vision groups them into one visual line. That the rectangles
are right is not assumed: ``electors.verify`` compares every tile's pixels against the source
page, and on the first five parts of AC1 all 2,964 were byte-identical.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from assam_rolls import schema

from . import crops, extract, repack, stage1, vision, vision_part


def composites(part_dir: Path) -> List[Path]:
    """The images stage one left, in the order it wrote them."""
    return sorted(part_dir.glob("composite*.png"))


def placements_of(part_dir: Path) -> Dict[str, List[repack.Placed]]:
    """Each composite's tiles, back as the objects the mapper expects.

    Rebuilt rather than re-derived. Re-deriving the layout here would let stage two disagree with
    the image stage one actually produced -- and the disagreement would look like a reading error,
    not a geometry error, because the words would come back plausible and misfiled.
    """
    raw = json.loads((part_dir / "placements.json").read_text(encoding="utf-8"))
    return {
        image: [
            repack.Placed(
                key=(tile["page"], tile["box_row"], tile["box_col"]),
                left=tile["left"],
                top=tile["top"],
                right=tile["right"],
                bottom=tile["bottom"],
            )
            for tile in tiles
        ]
        for image, tiles in raw.items()
    }


def identity(part_dir: Path) -> Dict[str, Any]:
    """Which part this is, taken from the manifest rather than from the directory name.

    A directory can be moved or renamed; the manifest carries the source filename and its sha, so
    the identity travels with the data and a row can still be traced to exact source bytes.
    """
    manifest = crops.read_manifest(part_dir)
    if not manifest:
        raise ValueError(f"{part_dir} has an empty manifest")
    row = next(iter(manifest.values()))
    return {
        "ac_no": row["ac_no"],
        "part_no": row["part_no"],
        "source_zip": row.get("source_zip", ""),
        "source_pdf": row["source_pdf"],
        "pdf_sha256": row["pdf_sha256"],
    }


def read_part(
    part_dir: Path,
    api_key: str,
    annotate: Optional[Callable[[Sequence[Any]], Sequence[Any]]] = None,
) -> extract.PartResult:
    """One prepared part through Vision, as rows.

    ``annotate`` is injectable so the mapping can be tested without spending anything: the join
    is the part that can be wrong, and it should not take an API key to check it.
    """
    from PIL import Image

    who = identity(part_dir)
    meta = schema.parse_source_filename(who["source_pdf"]) or {}
    result = extract.PartResult(
        ac_no=who["ac_no"],
        part_no=who["part_no"],
        lang=meta.get("lang", ""),
        source_zip=who["source_zip"],
        source_pdf=who["source_pdf"],
        pdf_sha256=who["pdf_sha256"],
    )
    try:
        side = stage1.read_side(part_dir / "side.json")
        result.unknown_pages = list(side["unknown"])
        result.page_count = len(side["pages"]) + len(side["unknown"])
        if side["summary"] is not None:
            closing = side["summary"]
            result.summary_male = closing.male
            result.summary_female = closing.female
            result.summary_third = closing.third
            result.summary_total = closing.total

        placements = placements_of(part_dir)
        words: Dict[Tuple[int, int, int], List[vision.Word]] = {}
        for path in composites(part_dir):
            placed = placements.get(path.name)
            if placed is None:
                raise ValueError(f"{path.name} has no placements -- cannot attribute its words")
            with Image.open(path) as image:
                image.load()
                responses = (
                    annotate([image])
                    if annotate is not None
                    else vision.annotate([image], api_key=api_key)
                )
            if not responses:
                raise RuntimeError(f"no response for {path.name}")
            words.update(repack.words_for(placed, vision.words_from(responses[0])))
            result.images_billed += 1

        vision_part._fill_repacked(result, side, words, meta)
    except (OSError, RuntimeError, ValueError) as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    return result


def ready(part_dir: Path) -> bool:
    """Whether stage one left this part complete enough to read."""
    return (
        (part_dir / crops.MANIFEST).exists()
        and (part_dir / "placements.json").exists()
        and (part_dir / "side.json").exists()
        and bool(composites(part_dir))
    )
