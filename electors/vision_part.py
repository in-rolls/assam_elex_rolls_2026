"""Reading a whole part through Cloud Vision.

The tesseract path in :mod:`electors.extract` is built around one page at a time, because that
is the unit tesseract charges for. Vision charges per **image submitted**, so the unit here is
several pages stacked into one tall PNG -- four at 400 dpi -- and a whole part is seven or
eight calls rather than nine hundred process spawns. That is the difference between $1,400 and
$368 for the state, and it is why this is a separate reader rather than an engine slotted into
the existing one.

**No tesseract anywhere in it.** The section header and the closing summary were the two places
the old path still needed a second engine, and Vision has already returned the words for both
by the time they are wanted -- the header sits above the first box, and the summary page is
just another page in the stack. Reading them from words that were already paid for removes the
engine rather than adding a fallback.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from PIL import Image

from assam_rolls import render, schema

from . import extract, grid, pages, repack
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


def tiles_for(
    images: Sequence[Image.Image],
) -> Tuple[List[Tuple[Image.Image, Tuple[int, int, int, int], Tuple[int, int, int]]], Dict]:
    """Every box's text lines as a tile, plus what the CPU needs to read for itself.

    Only the four labelled lines go to Vision. Three other things are read here on hardware
    already paid for, because none of them is worth a paid pixel:

    - the **EPIC** and the serial, which sit to the right of ``text_right`` in the photo column.
      Including them would mean submitting the full box width and giving back most of the
      saving. Read from their own cells with tesseract ``eng``, the EPIC comes back well formed
      on 96.7% of boxes -- against 62.3% for the single whole-strip pass this replaces, which
      asked for a boxed serial and an EPIC as though they were one line.
    - the **section header**, which decides whether serials restart.
    - the **closing summary**, which is the number every completeness check is measured against.
    """
    from . import crops as crop_module

    entries: List[Tuple[Image.Image, Tuple[int, int, int, int], Tuple[int, int, int]]] = []
    side: Dict[str, Any] = {"pages": {}, "unknown": [], "summary": None}

    # Classify every page first, then reconsider the ones that came out as anything but elector
    # pages. A part's last page can hold a single elector, and one page's rules cannot tell that
    # box from a row of the closing summary's table -- but the part's other pages can, because
    # they draw their columns in the same places. Part 18's serial 661 was lost exactly here.
    signatures = pages.recover_partial(
        [pages.classify(image, n) for n, image in enumerate(images, start=1)]
    )

    for signature, image in zip(signatures, images):
        index = signature.number
        if signature.kind is pages.PageKind.UNKNOWN:
            side["unknown"].append(index)
            continue
        if signature.kind is pages.PageKind.SUMMARY and side["summary"] is None:
            side["summary"] = _summary_from(image)
        if not signature.is_elector:
            continue
        boxes = grid.build(signature.h_rules, signature.v_rules, columns=signature.columns)
        if not boxes:
            side["unknown"].append(index)
            continue

        section, recognised = pages.section_of(_header_strip_text(image, boxes))
        side["pages"][index] = {
            "section": section,
            "recognised": recognised,
            "boxes": {},
        }
        for box in boxes:
            if not grid.has_ink(image, box):
                continue
            bands = grid.text_bands(image, box)
            if not bands:
                continue
            top = crop_module.band_window(bands, 0, box)[0]
            bottom = crop_module.band_window(bands, len(bands) - 1, box)[1]
            key = (index, box.row, box.column)
            entries.append((image, (box.left, top, box.text_right, bottom), key))
            side["pages"][index]["boxes"][key] = _header_of(image, box)
    return entries, side


#: The letters the roll uses to mark an entry that is no longer a live elector, defined by its
#: own legend: E dead, S shifted, R duplicate, M missing, Q ineligible. A closed set, so anything
#: else in that cell is a misread digit rather than a marking.
STATUS_CODES = "ESRMQ"


#: The serial cell's own ruled borders, as fractions of the box. Measured off the rules rather
#: than guessed: at 400 dpi a 1004x415 box rules that cell at x 15..328, y 15..77. Two earlier
#: attempts at these numbers by eye read 0 of 30 serials.
SERIAL_CELL = (0.020, 0.042, 0.325, 0.182)

#: Where the EPIC sits, as fractions of the box, with the right edge extended past it.
#:
#: The extension is the point. The EPIC is printed hard against the box's right rule, so cropping
#: at the rule slices the last character -- ``HHK0001457`` came back as ``HHK000145/``. Forty
#: pixels of margin costs nothing and takes the field from 88.9% to 96.7%; the whole-strip read
#: this replaces managed 62.3%.
EPIC_REGION = (0.55, 0.0, 1.0, 0.19)
EPIC_PAD = 40


def _read(image: Image.Image, rect: Tuple[int, int, int, int], psm: int) -> str:
    from assam_rolls import ocr

    try:
        engine = ocr.get_engine("tesseract", lang="eng")
        return " ".join(engine._run(image.crop(rect), "eng", None, 2, psm=psm).split())
    except Exception:
        return ""


def _header_of(image: Image.Image, box: grid.Box) -> Dict[str, str]:
    """The serial, the EPIC and the status code, each read from its own cell.

    Read with the English model: ``asm`` renders ``HHK0001471`` as ``1414140001471``, being
    trained on a script with no Latin letters and mapping them onto lookalike digits.

    Three crops rather than one. The old version OCR'd the whole strip in a single pass and got
    62.3% well-formed EPICs over 14,486 boxes -- the boxed serial and the EPIC are different
    kinds of thing sitting in different cells, and asking for both at once as a single line lost
    a fifth of them entirely.

    The **status code** is a letter the roll prints inside the serial cell, to the left of the
    number: ``E   15`` where a live entry shows ``15``. The roll defines them itself, in a legend
    at the foot of every closing page::

        E-মৃত, S-স্থানান্তৰিত/ বাসস্থান পৰিবৰ্তন, R-প্ৰতিলিপি, M-নিখোজ, Q-অযোগ্য

    dead, shifted, duplicate, missing, ineligible. All five mean the same thing for counting
    purposes -- not a live elector -- and nothing outside that set is a code. Accepting any
    letter read 5,167 across AC1 against 3,816 implied by the roll's own arithmetic, 34% over;
    restricting to the five leaves 3,826, which is 0.3% over. The 1,341 rejected were #, A, H
    and D: mangled digits, not markings.

    This is the signal because the watermark is not. Vision returns five Latin fragments from a
    whole 67 MP composite -- 'D', 'ED', 'RIZED' -- so reading the stamp itself finds nothing.
    """
    width = box.right - box.left
    height = box.bottom - box.top

    left, top, right, bottom = SERIAL_CELL
    cell = _read(
        image,
        (
            box.left + int(width * left),
            box.top + int(height * top),
            box.left + int(width * right),
            box.top + int(height * bottom),
        ),
        psm=7,
    )

    left, top, right, bottom = EPIC_REGION
    epic = _read(
        image,
        (
            box.left + int(width * left),
            box.top + int(height * top),
            min(image.width, box.left + int(width * right) + EPIC_PAD),
            box.top + int(height * bottom),
        ),
        psm=7,
    ).replace(" ", "")

    # A code only counts when it is a lone letter *followed by* the serial. Anything else in this
    # cell is a mangled digit, and treating those as codes would mark live electors dead.
    found = re.match(rf"^\s*([{STATUS_CODES}])[\s.]+\d", cell)
    return {
        "serial": re.sub(r"\D", "", cell),
        "epic": epic,
        "code": found.group(1).upper() if found else "",
    }


def _header_strip_text(image: Image.Image, boxes: Sequence[grid.Box]) -> str:
    top = min(boxes[0].top, int(image.height * 0.09))
    if top <= 0:
        return ""
    from assam_rolls import ocr

    try:
        engine = ocr.get_engine("tesseract", lang="asm")
        return engine._run(image.crop((0, 0, image.width, top)), "asm", None, 1, psm=6)
    except Exception:
        return ""


def _summary_from(image: Image.Image):
    """The closing totals, read on the CPU and only kept if the arithmetic balances."""
    from assam_rolls import ocr

    try:
        engine = ocr.get_engine("tesseract", lang="asm")
        return summary_page.read(engine, image)
    except Exception:
        return None


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


def read_repacked(
    zip_path: Path,
    pdf_name: str,
    api_key: str,
    renderer: Optional[Callable[[bytes, Path], List[Image.Image]]] = None,
) -> extract.PartResult:
    """One part, submitting only the text lines and nothing else.

    Same engine, same pixels of text, same parser as :func:`read_part` -- only the packing
    differs, which is why the two must agree almost exactly and any real gap is a mapping bug.
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
            images = (renderer or rasterize(extract.DPI))(pdf_bytes, Path(tmp))
            result.page_count = len(images)
            entries, side = tiles_for(images)
            result.unknown_pages = side["unknown"]
            if side["summary"] is not None:
                closing = side["summary"]
                result.summary_male = closing.male
                result.summary_female = closing.female
                result.summary_third = closing.third
                result.summary_total = closing.total

            words: Dict[Tuple[int, int, int], List[vision.Word]] = {}
            budget = int(vision.MAX_PIXELS * vision.PIXEL_MARGIN)
            for batch in repack.batches(entries, budget):
                words.update(_read_composite(batch, api_key))
                result.images_billed += 1

            _fill_repacked(result, side, words, meta)
            for image in images:
                image.close()
    except (subprocess.CalledProcessError, OSError, RuntimeError, ValueError) as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    return result


def _read_composite(batch: Sequence, api_key: str) -> Dict[Tuple[int, int, int], List]:
    composite, placed = repack.compose(batch)
    responses = vision.annotate([composite], api_key=api_key)
    if not responses:
        raise RuntimeError("no response for a composite")
    return repack.words_for(placed, vision.words_from(responses[0]))


def _fill_repacked(
    result: extract.PartResult,
    side: Dict[str, Any],
    words: Dict[Tuple[int, int, int], List],
    meta: Dict[str, Any],
) -> None:
    """Rows from the tiles' words, in page order, with the serial counted the same way."""
    serial = 0
    current_section = pages.Section.MAIN
    for index in sorted(side["pages"]):
        page = side["pages"][index]
        result.elector_pages += 1
        if not page["recognised"]:
            result.unrecognised_headers.append(index)
        section = page["section"]
        if section is not pages.Section.MAIN:
            result.supplement_pages.append(index)
        if section is not current_section:
            current_section, serial = section, 0

        for key in sorted(page["boxes"]):
            found = words.get(key, [])
            if not found:
                continue
            width = max((w.right for w in found), default=0) + 1
            body = vision.lines_by_position(found, 0, width)
            # The header strip never went to Vision -- the EPIC is outside the text column, and
            # tesseract's English model reads it for nothing on hardware already paid for.
            side_read = page["boxes"][key]
            elector = vision.elector_from([side_read["epic"]], body)
            # The roll's own markings, read from their own cells rather than inferred from the
            # words: a status code means this entry is no longer a live elector.
            elector.status_code = side_read.get("code", "")
            elector.deleted = bool(elector.status_code)
            if side_read.get("serial", "").isdigit():
                elector.serial_no_ocr = int(side_read["serial"])
            if elector.is_empty:
                elector.flags.append("unreadable")
            serial += 1
            elector.serial_no = serial
            box = grid.Box(
                row=key[1],
                column=key[2],
                left=0,
                top=0,
                right=0,
                bottom=0,
                text_right=0,
            )
            result.electors.append(
                extract._row(result, elector, page=index, box=box, meta=meta, section=section)
            )


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
