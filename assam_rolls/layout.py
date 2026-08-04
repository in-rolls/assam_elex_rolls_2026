"""Recover the info-page table grid from ruled lines -- no OCR, no language model.

Page 1 is a machine-generated form, so its rules land on the same pixels every time.
Measured across AC1, AC10, AC12 and AC100:

* horizontal rules at ``124, 157, 191, 225, 399, 433, 483, 816, 850, 896`` are present on
  every page, pixel-identical;
* the section-4 elector table is always the **last five** rules, with fixed relative
  spacing (``+34, +80, +116, +170``);
* its six columns sit at ``24, 156, 286, 497, 723, 947, 1162``, identical within 1px.

Two page variants exist. Some pages carry an extra rule around y=171, and the section-3
address block is 26px taller when the address wraps to a second line -- which shifts
everything below it. So cells are addressed by **detected structure** (stable rules from
the top, the elector table from the end), never by hardcoded absolute offsets.

Recovering the grid mechanically means label text never has to be read: the pipeline
knows where each value lives and OCRs only that cell. That is what makes a free,
local-only pipeline viable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

#: Rules present on every page observed, used to verify a page matches the template.
STABLE_H_RULES: Tuple[int, ...] = (124, 157, 191, 225, 399, 433, 483, 816, 850, 896)

#: The elector table is always the last five horizontal rules.
S4_RULE_COUNT = 5

#: Tolerance when matching a detected rule to its expected position.
RULE_TOLERANCE = 4

#: Ink threshold: below this grey value a pixel counts as part of a rule.
INK = 128

#: A row/column is a rule when this share of the sampled span is ink.
H_RULE_FILL = 0.5
V_RULE_FILL = 0.6

#: Page frame, used to bound sampling.
FRAME_LEFT, FRAME_RIGHT = 24, 1162

#: Height of the footer strip carrying the total-pages line.
FOOTER_HEIGHT = 100

#: Native page size of these scans; used by tests and as a sanity hint.
EXPECTED_SIZE_HINT = (1187, 1679)


class LayoutError(RuntimeError):
    """Raised when a page does not match the info-page template."""


@dataclass(frozen=True)
class Box:
    """A cell in page pixel coordinates."""

    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def size(self) -> Tuple[int, int]:
        return self.x1 - self.x0, self.y1 - self.y0

    def pad(self, inset: int = 2) -> "Box":
        """Shrink by ``inset`` px so the surrounding rules are not fed to OCR."""
        return Box(self.x0 + inset, self.y0 + inset, self.x1 - inset, self.y1 - inset)


@dataclass(frozen=True)
class Grid:
    """A page's detected geometry and its named cells."""

    width: int
    height: int
    h_rules: Tuple[int, ...]
    cells: Dict[str, Box]

    def crop(self, image: Image.Image, name: str, inset: int = 2) -> Image.Image:
        box = self.cells[name].pad(inset)
        return image.crop((box.x0, box.y0, box.x1, box.y1))


# --------------------------------------------------------------------------- detection


def _group(indices: Sequence[int], gap: int = 2) -> List[int]:
    """Collapse runs of adjacent indices into their midpoints."""
    groups: List[List[int]] = []
    for index in indices:
        if groups and index - groups[-1][-1] <= gap:
            groups[-1].append(index)
        else:
            groups.append([index])
    return [sum(group) // len(group) for group in groups]


def detect_h_rules(image: Image.Image) -> List[int]:
    """Y positions of full-width horizontal rules."""
    grey = image.convert("L")
    width, height = grey.size
    pixels = grey.load()
    left, right = FRAME_LEFT, min(FRAME_RIGHT, width)
    step = 3
    samples = len(range(left, right, step))
    hits = [
        y
        for y in range(height)
        if sum(1 for x in range(left, right, step) if pixels[x, y] < INK) > samples * H_RULE_FILL
    ]
    return _group(hits)


def detect_v_rules(image: Image.Image, y0: int, y1: int) -> List[int]:
    """X positions of vertical rules spanning the band ``y0..y1``."""
    grey = image.convert("L")
    width, _ = grey.size
    pixels = grey.load()
    span = max(1, y1 - y0)
    hits = [
        x
        for x in range(width)
        if sum(1 for y in range(y0, y1) if pixels[x, y] < INK) > span * V_RULE_FILL
    ]
    return _group(hits)


def _match(rules: Sequence[int], expected: int) -> Optional[int]:
    """The detected rule closest to ``expected``, within tolerance."""
    candidates = [r for r in rules if abs(r - expected) <= RULE_TOLERANCE]
    return min(candidates, key=lambda r: abs(r - expected)) if candidates else None


def _column(v_rules: Sequence[int], expected: int, fallback: int) -> int:
    """Snap to a detected vertical rule, falling back to the template position."""
    match = _match(v_rules, expected)
    return match if match is not None else fallback


# ------------------------------------------------------------------------------- grid


def build_grid(image: Image.Image) -> Grid:
    """Detect the grid and map it to named cells.

    Raises ``LayoutError`` when the stable rules are absent, which is the signal that a
    page is not the standard info-page template and should go to review rather than
    silently produce garbage cells.
    """
    width, height = image.size
    h_rules = detect_h_rules(image)

    anchors: Dict[int, int] = {}
    missing: List[int] = []
    for expected in STABLE_H_RULES:
        found = _match(h_rules, expected)
        if found is None:
            missing.append(expected)
        else:
            anchors[expected] = found
    if missing:
        raise LayoutError(
            f"page does not match the info-page template; missing rules at {missing} "
            f"(found {len(h_rules)}: {h_rules[:16]})"
        )

    if len(h_rules) < len(STABLE_H_RULES) + S4_RULE_COUNT:
        raise LayoutError(
            f"expected at least {len(STABLE_H_RULES) + S4_RULE_COUNT} rules, got {len(h_rules)}"
        )

    # Section 4 is anchored from the end: its header, two header rows, then the values.
    s4_rules = h_rules[-S4_RULE_COUNT:]
    s4_top, value_top, value_bottom = s4_rules[0], s4_rules[-2], s4_rules[-1]

    top = anchors  # shorthand for the stable rules

    # Vertical rules are detected per band; the page-wide scan misses short dividers.
    header_v = detect_v_rules(image, top[124], top[157])
    s1_v = detect_v_rules(image, top[225], top[399])
    s2_v = detect_v_rules(image, top[483], top[816])
    s3_v = detect_v_rules(image, top[850], top[896])
    s4_v = detect_v_rules(image, value_top, value_bottom)

    part_split = _column(header_v, 947, 947)
    s1_split = _column(s1_v, 497, 497)
    s2_split = _column(s2_v, 502, 502)
    s3_split = _column(s3_v, 497, 497)
    s3_right = _column(s3_v, 947, 947)

    # Six elector columns; fall back to template positions if a divider is faint.
    defaults = (24, 156, 286, 497, 723, 947, 1162)
    cols = [_column(s4_v, expected, expected) for expected in defaults]

    cells: Dict[str, Box] = {
        # header
        "header_ac": Box(FRAME_LEFT, top[124], part_split, top[157]),
        "header_part_no": Box(part_split, top[124], FRAME_RIGHT, top[157]),
        "header_pc": Box(FRAME_LEFT, top[157], FRAME_RIGHT, top[191]),
        # section 1 -- revision
        "s1_revision": Box(FRAME_LEFT, top[225], s1_split, top[399]),
        "s1_description": Box(s1_split, top[225], FRAME_RIGHT, top[399]),
        # section 2 -- areas and locality
        "s2_areas": Box(FRAME_LEFT, top[483], s2_split, top[816]),
        "s2_locality": Box(s2_split, top[483], FRAME_RIGHT, top[816]),
        # section 3 -- polling station
        "s3_ps_name": Box(FRAME_LEFT, top[850], s3_split, top[896]),
        "s3_type_value": Box(s3_right, top[850], FRAME_RIGHT, top[896]),
        "s3_address": Box(FRAME_LEFT, top[896], s3_split, s4_top),
        "s3_aux_value": Box(s3_right, top[896], FRAME_RIGHT, s4_top),
        # section 4 -- electors (pure digits)
        "s4_start_serial": Box(cols[0], value_top, cols[1], value_bottom),
        "s4_end_serial": Box(cols[1], value_top, cols[2], value_bottom),
        "s4_male": Box(cols[2], value_top, cols[3], value_bottom),
        "s4_female": Box(cols[3], value_top, cols[4], value_bottom),
        "s4_third_gender": Box(cols[4], value_top, cols[5], value_bottom),
        "s4_total": Box(cols[5], value_top, cols[6], value_bottom),
        # footer -- "মুঠ পৃষ্ঠা <N> - পৃষ্ঠা 1" sits in the last inch of the page
        "footer": Box(FRAME_LEFT, height - FOOTER_HEIGHT, FRAME_RIGHT, height),
    }

    return Grid(width=width, height=height, h_rules=tuple(h_rules), cells=cells)


#: Cells whose content is digits only -- OCR'd with a digit whitelist and verified by
#: the hard checks in ``validate.py``.
NUMERIC_CELLS = (
    "s4_start_serial",
    "s4_end_serial",
    "s4_male",
    "s4_female",
    "s4_third_gender",
    "s4_total",
    "s3_aux_value",
)

#: Cells holding Assamese script, where engine choice actually matters.
TEXT_CELLS = (
    "header_ac",
    "header_pc",
    "s1_revision",
    "s1_description",
    "s2_areas",
    "s2_locality",
    "s3_ps_name",
    "s3_address",
    "s3_type_value",
)


def debug_overlay(image: Image.Image, grid: Grid) -> Image.Image:
    """Draw the detected cells on a copy of the page, so a bad grid is obvious."""
    canvas = image.convert("RGB").copy()
    draw = ImageDraw.Draw(canvas)
    for name, box in grid.cells.items():
        colour = (200, 30, 30) if name in NUMERIC_CELLS else (30, 90, 200)
        draw.rectangle([box.x0, box.y0, box.x1, box.y1], outline=colour, width=2)
        draw.text((box.x0 + 4, box.y0 + 2), name, fill=colour)
    return canvas
