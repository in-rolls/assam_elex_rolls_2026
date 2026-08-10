"""Reading a whole part through Cloud Vision.

The tesseract path in :mod:`electors.extract` is built around one page at a time, because that
is the unit tesseract charges for. Vision charges per **image submitted**, so the unit here is
eight pages stacked into one tall PNG, and the whole part is four or five calls rather than
nine hundred process spawns. That is the difference between $1,400 and $173 for the state, and
it is why this is a separate reader rather than an engine slotted into the existing one.

**No tesseract anywhere in it.** The section header and the closing summary were the two places
the old path still needed a second engine, and Vision has already returned the words for both
by the time they are wanted -- the header sits above the first box, and the summary page is
just another page in the stack. Reading them from words that were already paid for removes the
engine rather than adding a fallback.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from PIL import Image

from assam_rolls import render, schema

from . import extract, grid, pages
from . import summary as summary_page
from . import vision

#: One page of a stack, and everything Vision said about it.
PageWords = Tuple[int, Image.Image, List[vision.Word]]


def _chunks(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def words_per_page(
    images: Sequence[Image.Image],
    api_key: str,
    pages_per_image: Optional[int] = None,
    images_per_request: int = vision.IMAGES_PER_REQUEST,
) -> Tuple[List[List[vision.Word]], int]:
    """Every page's words, and the number of images billed.

    Pages are stacked, the stacks are batched, and the words are shifted back into each page's
    own coordinates on the way out. That last step is the one that would fail silently: a word
    on page two of a stack comes back in stack coordinates, and left unshifted it lands in
    whichever box happens to sit at that height on page one -- a plausible wrong answer on every
    page but the first.

    The stacking factor is measured from the pages unless it is given, because it depends on the
    render resolution and a constant that disagreed with it refused every part.
    """
    if not images:
        return [], 0
    if pages_per_image is None:
        widest = max(image.width for image in images)
        tallest = max(image.height for image in images)
        pages_per_image = vision.pages_that_fit(
            widest, tallest, bytes_per_page=vision.encoded_size(images[0])
        )
    stacks = [vision.stack_pages(group) for group in _chunks(images, pages_per_image)]
    for stack in stacks:
        problem = stack.check()
        if problem:
            raise ValueError(f"stack rejected before spending a request: {problem}")

    # Batched by payload, not by count. Sixteen images are legal individually and 96 MB
    # together, and the request ceiling is 40 MB -- which rejected every 400 dpi and 300 dpi
    # part while letting native through, and would have read as "the expensive arms do not
    # work" rather than "the batcher does not".
    encoded = [vision.encode(stack.image) for stack in stacks]
    groups = vision.batch_images(encoded)

    # Re-chunked rather than truncated. ``batch_images`` already caps at the module's own
    # per-request count, but a caller passing a smaller one would otherwise have the surplus
    # stacks silently dropped -- pages missing from the output with nothing raised.
    groups = [
        g[i : i + images_per_request] for g in groups for i in range(0, len(g), images_per_request)
    ]

    out: List[List[vision.Word]] = []
    billed = 0
    for group in groups:
        batch = [stacks[i] for i in group]
        responses = vision.annotate([stack.image for stack in batch], api_key=api_key)
        billed += len(batch)
        if len(responses) != len(batch):
            raise RuntimeError(f"asked for {len(batch)} images, got {len(responses)} responses")
        for stack, response in zip(batch, responses):
            words = vision.words_from(response)
            bounds = list(zip(stack.offsets, stack.offsets[1:] + [stack.image.height]))
            for top, bottom in bounds:
                page = [w.shifted(top) for w in words if top <= w.middle_y < bottom]
                out.append(page)
    return out[: len(images)], billed


def _header_text(
    words: Sequence[vision.Word], image: Image.Image, boxes: Sequence[grid.Box]
) -> str:
    """The band above the first box, which says which list the page belongs to."""
    top = min(boxes[0].top, int(image.height * 0.09))
    above = [w for w in words if w.bottom <= top]
    above.sort(key=lambda w: (w.top, w.left))
    return " ".join(w.text for w in above)


def _page_text(words: Sequence[vision.Word], image: Image.Image) -> str:
    """A page as lines, for the closing-total parser.

    Real lines, not one long string: ``summary.parse`` takes the last balancing triple *on a
    line*, because the row's prose runs ahead of its figures and a year can precede the real
    numbers. Flattened to a single line, a page with several rows would offer up the last row's
    figures for the main list.
    """
    return "\n".join(vision.grouped_lines(words, 0, image.width))


def rasterize(dpi: int) -> Callable[[bytes, Path], List[Image.Image]]:
    """A renderer that rasterizes every page at ``dpi``."""

    def renderer(pdf_bytes: bytes, workdir: Path) -> List[Image.Image]:
        paths = extract._render_pages(pdf_bytes, workdir, dpi=dpi)
        return [Image.open(path).convert("L") for path in paths]

    return renderer


def read_part(
    zip_path: Path,
    pdf_name: str,
    api_key: str,
    pages_per_image: Optional[int] = None,
    renderer: Optional[Callable[[bytes, Path], List[Image.Image]]] = None,
) -> extract.PartResult:
    """Extract every elector from one part PDF, using Cloud Vision for all of it.

    ``renderer`` decides what pixels Vision is shown. It is pluggable because the source PDFs
    are 144 dpi scans -- rendering at 400 upsamples them -- and what resolution to submit is a
    measured question rather than a settled one. Everything after the renderer is identical
    across resolutions, which is what makes the arms comparable.
    """
    meta = schema.parse_source_filename(pdf_name) or {}
    pdf_bytes = render.read_pdf_bytes(zip_path, pdf_name)
    result = extract.PartResult(
        ac_no=meta.get("ac_no", 0),
        part_no=meta.get("part_no", 0),
        lang=meta.get("lang", ""),
        source_zip=zip_path.name,
        source_pdf=pdf_name,
        pdf_sha256=render.sha256_bytes(pdf_bytes),
    )

    try:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            images = (renderer or rasterize(extract.DPI))(pdf_bytes, workdir)
            result.page_count = len(images)
            per_page, result.images_billed = words_per_page(
                images, api_key=api_key, pages_per_image=pages_per_image
            )
            _fill(result, images, per_page, meta)
            for image in images:
                image.close()
    except (subprocess.CalledProcessError, OSError, RuntimeError, ValueError) as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    return result


def _fill(
    result: extract.PartResult,
    images: Sequence[Image.Image],
    per_page: Sequence[Sequence[vision.Word]],
    meta: Dict[str, Any],
) -> None:
    """Turn each page's words into rows, in page order."""
    serial = 0
    current_section = pages.Section.MAIN

    for index, (image, words) in enumerate(zip(images, per_page), start=1):
        signature = pages.classify(image, index)
        if signature.kind is pages.PageKind.UNKNOWN:
            result.unknown_pages.append(index)
            continue
        if signature.kind is pages.PageKind.SUMMARY and result.summary_total is None:
            found = summary_page.parse(_page_text(words, image))
            if found:
                male, female, third, total = found
                # The same arithmetic check the tesseract path applies. A triple that does not
                # balance is a misread, and recording it would give every completeness check a
                # wrong denominator to agree with.
                closing = summary_page.RollSummary(
                    male=male, female=female, third=third, total=total, scale=1
                )
                if closing.balances:
                    result.summary_male = male
                    result.summary_female = female
                    result.summary_third = third
                    result.summary_total = total
        if not signature.is_elector:
            continue

        boxes = grid.build(signature.h_rules, signature.v_rules)
        if not boxes:
            # Classified as an elector page but its geometry did not resolve. Recorded rather
            # than parsed: guessing thirty box positions here would produce thirty plausible
            # rows of nothing.
            result.unknown_pages.append(index)
            continue
        result.elector_pages += 1

        section, recognised = pages.section_of(_header_text(words, image, boxes))
        if not recognised:
            result.unrecognised_headers.append(index)
        if section is not pages.Section.MAIN:
            result.supplement_pages.append(index)
        # Each list is numbered from 1 in the source, so the counter restarts when the section
        # changes rather than running through as if it were one roll.
        if section is not current_section:
            current_section, serial = section, 0

        for box in boxes:
            inside = vision.words_within(words, box.left, box.top, box.text_right, box.bottom)
            header, body = vision.header_and_body(inside, box.left, box.text_right)
            if not header and not body:
                # Vision found no words in the box at all. A blank box at the end of a part is
                # the publisher's doing, and there is nothing in it to read.
                continue
            elector = vision.elector_from(header, body)
            if elector.is_empty:
                # Words but nothing readable. Emitted anyway: the row exists in the source, and
                # dropping it makes the count agree with nothing.
                elector.flags.append("unreadable")
            serial += 1
            elector.serial_no = serial
            result.electors.append(
                extract._row(result, elector, page=index, box=box, meta=meta, section=section)
            )


def parts_cost(page_counts: Sequence[int], pages_per_image: int = vision.PAGES_PER_IMAGE) -> float:
    """Dollars to read parts of these page counts, billed as Vision bills."""
    images = sum(-(-count // pages_per_image) for count in page_counts)
    return vision.cost(images)


def summarise(results: Sequence[extract.PartResult]) -> Dict[str, Any]:
    """What a run of parts came to, in the terms the bill and the roll are both in."""
    done = [r for r in results if not r.error]
    billed = sum(r.images_billed for r in results)
    return {
        "parts": len(results),
        "failed": len(results) - len(done),
        "electors": sum(r.count for r in done),
        "pages": sum(r.page_count for r in done),
        "images_billed": billed,
        "dollars": vision.cost(billed),
    }
