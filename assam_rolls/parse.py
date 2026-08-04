"""Turn a detected grid plus an OCR engine into schema rows.

Every field is read from a cell whose position is known, so the parser never has to
recognise a label to find a value. Two structures recur and are handled positionally:

* **Stacked label/value blocks** (section 1's revision details, section 2's locality
  block) are split into text rows by ink profile, then each row is cropped to the value
  column. Field identity comes from the **row index**, not from reading the label --
  Tesseract mangles labels (``ব্লক`` becomes ``বক``) and sometimes reads the colon as
  ``£``, both of which would break a text-based split.
* **Wrapped values.** The polling-station name is printed under its label, so it lands
  in the ``s3_address`` cell rather than ``s3_ps_name``; the address follows its own
  label row. Both are addressed by row index within that cell.

Numbers are only ever read with ``read_digits`` from digit-only crops -- see ``ocr`` for
why mixing the Assamese model into number reading corrupts results silently.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from .layout import Grid
from .ocr import Engine, int_or_none, value_after_label
from .render import PartRef
from .schema import (
    PS_TYPE_FROM_ASSAMESE,
    RESERVATION_FROM_ASSAMESE,
    clean_text,
    derive_columns,
    empty_part_row,
    normalize_digits,
)

#: Row order of the section-2 locality block, top to bottom.
LOCALITY_FIELDS = (
    "main_town_village",
    "ward_no",
    "post_office",
    "police_station",
    "block",
    "revenue_circle",
    "district",
    "pin_code",
)

#: Row order of the section-1 revision block.
REVISION_FIELDS = (
    "revision_year",
    "qualifying_date",
    "revision_type",
    "publication_date",
)

#: Fallback X offset where values begin, used when a row has no label/value gap wide
#: enough to locate (which is what an empty value looks like).
LOCALITY_VALUE_X = 320
REVISION_VALUE_X = 213

#: A label is separated from its value by a wide run of whitespace; word spaces inside a
#: label are far narrower. Measured gaps are ~95-131px against ~10px word spacing.
MIN_LABEL_GAP = 40

DATE_RE = re.compile(r"(\d{2})-(\d{2})-(\d{4})")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
#: "<n> - <name> (<reservation>)" as printed in the AC/PC header and the station name.
NUMBERED_NAME_RE = re.compile(r"^\s*(\d+)\s*[-–]\s*(.*?)\s*$")


def text_rows(
    image: Image.Image, gap: int = 4, min_ink: int = 2, ink: int = 150
) -> List[Tuple[int, int]]:
    """Y extents of each text row in a cell, found from the horizontal ink profile."""
    width, height = image.size
    pixels = image.convert("L").load()
    hits = [
        y for y in range(height) if sum(1 for x in range(width) if pixels[x, y] < ink) > min_ink
    ]
    groups: List[List[int]] = []
    for y in hits:
        if groups and y - groups[-1][-1] <= gap:
            groups[-1].append(y)
        else:
            groups.append([y])
    return [(g[0], g[-1]) for g in groups]


def _row_crop(cell: Image.Image, span: Tuple[int, int], x0: int = 0, pad: int = 4):
    top = max(0, span[0] - pad)
    bottom = min(cell.height, span[1] + pad + 1)
    return cell.crop((max(0, x0), top, cell.width, bottom))


def value_start_x(cell: Image.Image, span: Tuple[int, int], default: int, ink: int = 150) -> int:
    """Where the value begins on a ``label   value`` row.

    Found from the widest run of whitespace in the row: a label is separated from its
    value by a gap far wider than the spaces inside it. This self-calibrates, which
    matters because the revision block's values are identical corpus-wide and so cannot
    be located by comparing pages. Falls back to ``default`` when no wide gap exists --
    the signature of an empty value.
    """
    pixels = cell.convert("L").load()
    columns = [
        x for x in range(cell.width) if any(pixels[x, y] < ink for y in range(span[0], span[1] + 1))
    ]
    if len(columns) < 2:
        return default
    width, start = max(((b - a, a) for a, b in zip(columns, columns[1:])), default=(0, 0))
    return start + width if width >= MIN_LABEL_GAP else default


#: Characters Tesseract produces where the form prints a colon. It reads the locality
#: block's colon as "£" often enough that stripping only ":" leaves it in the value.
COLON_LIKE = ":;£¢|"


def strip_leading_colon(text: str) -> str:
    """Drop a leading colon (however it was transcribed) from a value."""
    return clean_text(text or "").lstrip(COLON_LIKE + " ").strip()


def iso_date(text: str) -> str:
    """``01-01-2026`` as printed becomes ``2026-01-01``."""
    match = DATE_RE.search(text or "")
    if not match:
        return ""
    day, month, year = match.groups()
    return f"{year}-{month}-{day}"


def split_numbered_name(value: str) -> Tuple[Optional[int], str]:
    """``"1 - গোসাইগাঁও (সাধাৰণ)"`` becomes ``(1, "গোসাইগাঁও (সাধাৰণ)")``."""
    match = NUMBERED_NAME_RE.match(value or "")
    if not match:
        return int_or_none(value), clean_text(value)
    return int(match.group(1)), clean_text(match.group(2))


def split_reservation(name: str) -> Tuple[str, str]:
    """Separate a trailing ``(reservation)`` from a constituency name."""
    match = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", name or "")
    if not match:
        return clean_text(name), ""
    bare, bracket = clean_text(match.group(1)), clean_text(match.group(2))
    return bare, RESERVATION_FROM_ASSAMESE.get(bracket, "")


def normalize_ps_type(value: str) -> str:
    return PS_TYPE_FROM_ASSAMESE.get(clean_text(value), "")


# ------------------------------------------------------------------------------ blocks


def _stacked_block(
    cell: Image.Image, fields: Tuple[str, ...], default_x: int, dynamic: bool = False
) -> Dict[str, Image.Image]:
    """Crop a stacked label/value block to one value image per field, by row index.

    ``dynamic`` locates the label/value boundary from the widest whitespace run, which
    the revision block needs because its values are constant corpus-wide and its label
    widths differ per row. The locality block instead has a fixed colon column, and a
    dynamic boundary there would land *before* the colon and pull it into the value.
    """
    crops: Dict[str, Image.Image] = {}
    for index, span in enumerate(text_rows(cell)):
        if index >= len(fields):
            break
        x0 = value_start_x(cell, span, default_x) if dynamic else default_x
        crops[fields[index]] = _row_crop(cell, span, x0)
    return crops


def parse_locality(cell: Image.Image, engine: Engine) -> Dict[str, Any]:
    crops = _stacked_block(cell, LOCALITY_FIELDS, LOCALITY_VALUE_X)
    parsed: Dict[str, Any] = {name: "" for name in LOCALITY_FIELDS}
    for name, crop in crops.items():
        # The pincode is printed in Latin digits, so read it with the digit engine
        # rather than the Assamese one, which can render an 8 as ৪.
        if name == "pin_code":
            parsed[name] = int_or_none(engine.read_digits(crop))
        else:
            parsed[name] = strip_leading_colon(engine.read_text(crop))
    return parsed


def parse_revision(cell: Image.Image, engine: Engine) -> Dict[str, Any]:
    crops = _stacked_block(cell, REVISION_FIELDS, REVISION_VALUE_X, dynamic=True)
    values = {name: engine.read_text(crop) for name, crop in crops.items()}
    return {
        "revision_year": int_or_none(values.get("revision_year", "")),
        "qualifying_date": iso_date(values.get("qualifying_date", "")),
        "revision_type": clean_text(values.get("revision_type", "")),
        "publication_date": iso_date(values.get("publication_date", "")),
    }


def parse_station(grid: Grid, image: Image.Image, engine: Engine) -> Dict[str, Any]:
    """Polling station number, name and address.

    The name is printed *below* its label, so it lands in the address cell's first row;
    the address itself follows its own label row.
    """
    cell = grid.crop(image, "s3_address")
    spans = text_rows(cell)
    lines = [engine.read_text(_row_crop(cell, span)) for span in spans]

    ps_no, ps_name = (None, "")
    if lines:
        ps_no, ps_name = split_numbered_name(lines[0])

    # Everything after the address label row is the address, which may wrap.
    address_parts: List[str] = []
    seen_label = False
    for line in lines[1:]:
        if not seen_label and ":" in line:
            seen_label = True
            tail = value_after_label(line)
            if tail:
                address_parts.append(tail)
            continue
        if seen_label:
            address_parts.append(line)
    if not seen_label:
        address_parts = lines[1:]

    return {
        "ps_no": ps_no,
        "ps_name": clean_text(ps_name),
        "ps_address": clean_text(" ".join(p for p in address_parts if p)),
    }


def parse_sections(grid: Grid, image: Image.Image, engine: Engine) -> List[Dict[str, Any]]:
    """The numbered area list, one entry per printed line."""
    cell = grid.crop(image, "s2_areas")
    rows: List[Dict[str, Any]] = []
    for index, span in enumerate(text_rows(cell), start=1):
        line = clean_text(engine.read_text(_row_crop(cell, span)))
        if not line:
            continue
        number, name = split_numbered_name(line)
        rows.append(
            {
                "section_no": number if number is not None else index,
                "section_name": name,
                "section_name_digits": normalize_digits(name),
                "section_name_roman": "",
            }
        )
    return rows


# -------------------------------------------------------------------------------- page


def parse_page(
    image: Image.Image, grid: Grid, ref: PartRef, engine: Engine
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Read one page into a part row plus its section rows."""
    row = empty_part_row()

    # Provenance. ac_no/part_no come from the filename, which is authoritative publisher
    # metadata; the OCR readings below are kept only as a quality signal.
    row.update(
        {
            "source_zip": ref.zip_name,
            "source_pdf": ref.pdf_name,
            "ac_no_file": ref.ac_no,
            "part_no_file": ref.part_no,
            "model": getattr(engine, "name", "unknown"),
        }
    )

    ac_no, ac_rest = split_numbered_name(
        value_after_label(engine.read_text(grid.crop(image, "header_ac")))
    )
    ac_name, ac_reservation = split_reservation(ac_rest)
    pc_no, pc_rest = split_numbered_name(
        value_after_label(engine.read_text(grid.crop(image, "header_pc")))
    )
    pc_name, pc_reservation = split_reservation(pc_rest)

    row.update(
        {
            "ac_no": ac_no,
            "ac_name": ac_name,
            "ac_reservation": ac_reservation,
            "pc_no": pc_no,
            "pc_name": pc_name,
            "pc_reservation": pc_reservation,
            "part_no": int_or_none(
                value_after_label(engine.read_text(grid.crop(image, "header_part_no")))
            ),
        }
    )

    row.update(parse_revision(grid.crop(image, "s1_revision"), engine))

    description = engine.read_text(grid.crop(image, "s1_description"))
    row["roll_description"] = clean_text(description)
    years = [int(y.group()) for y in YEAR_RE.finditer(description)]
    row["mother_roll_year"] = min(years) if years else None

    row.update(parse_locality(grid.crop(image, "s2_locality"), engine))
    row.update(parse_station(grid, image, engine))
    row["ps_type"] = normalize_ps_type(engine.read_text(grid.crop(image, "s3_type_value")))
    row["auxiliary_ps_count"] = int_or_none(engine.read_digits(grid.crop(image, "s3_aux_value")))

    for cell_name, field in (
        ("s4_start_serial", "start_serial"),
        ("s4_end_serial", "end_serial"),
        ("s4_male", "electors_male"),
        ("s4_female", "electors_female"),
        ("s4_third_gender", "electors_third_gender"),
        ("s4_total", "electors_total"),
    ):
        row[field] = int_or_none(engine.read_digits(grid.crop(image, cell_name)))

    row["total_pages"] = int_or_none(engine.read_text(grid.crop(image, "footer")))
    row["template_match"] = True
    row.update(derive_columns(row))

    sections = [
        {"ac_no": ref.ac_no, "part_no": ref.part_no, **section}
        for section in parse_sections(grid, image, engine)
    ]
    return row, sections
