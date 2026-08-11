"""The 3x10 grid of elector boxes, and the text lines inside each one.

Two levels of structure, found two different ways, because they are two different things.

**The boxes are ruled**, so they come from the rule detector already used on the info pages.
The columns arrive as triples -- outer left, an internal divider, outer right -- because each
box is split into a text column and a photo column. That divider is not noise to be filtered
out; it is the most useful line on the page (see below).

**The lines inside a box are not ruled**, so they come from an ink projection: count dark
pixels per scanline, and the text bands are the runs where that count is high. This is the
same idea as ``layout.detect_h_rules``, with the threshold lowered from "a printed rule" to
"some ink".

Two findings from measuring real pages, both of which cost a wrong answer first:

**The photo column must be excluded before projecting.** OCR of a whole box silently drops
the elector's name: *ফটো উপলব্ধ* ("photo available") sits beside the text at the same height,
and tesseract merges the two columns into one line. Cropping to the text column first turned
an unreadable box into four clean bands.

**The border must be excluded too.** A box's top rule is dark across its full width, so it
dominates the projection; against a threshold relative to that maximum, the EPIC number below
it disappears. ``parse.py`` hit exactly this on the info pages and solved it the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

#: Where the photo divider sits, as a fraction of box width. Measured on full pages:
#: (854-78)/(1082-78) = 0.773. Used only when the divider was never printed.
TEXT_FRACTION = 0.773

#: Rules closer together than this fraction of the page width belong to the same edge.
#: Adjacent boxes share a border drawn as two or three closely-spaced lines, and the page
#: border adds another beside the outermost.
#: Boxes per elector page. Fixed by the publisher's template, and asserted rather than
#: assumed -- a page that yields a different count is reported, not silently accepted.
COLUMNS = 3
ROWS = 10

#: A scanline is "inked" when this fraction of the crop's darkest scanline is present. The
#: fraction is deliberately low: name text is much lighter than a printed rule.
INK_FRACTION = 0.10

#: Below this, a pixel counts as ink. Scans here are grey rather than black.
INK_LEVEL = 160

#: A text band shorter than this is a speck, not a line.
MIN_BAND_HEIGHT = 8

#: Grown by this many pixels each way before OCR: Assamese ascenders and the matra sit
#: outside the ink run that found the band.
BAND_PAD = 5

#: Fraction of a box's height taken by its top border, skipped before projecting.
BORDER_SKIP = 0.04


@dataclass(frozen=True)
class Box:
    """One elector's cell: where it is on the page, and where its text lives inside it."""

    row: int
    column: int
    left: int
    top: int
    right: int
    bottom: int
    #: Right edge of the text column -- everything past this is the photo placeholder.
    text_right: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def text_width(self) -> int:
        """Width of the text column, excluding the photo placeholder."""
        return self.text_right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def text_crop(self, image: Image.Image) -> Image.Image:
        return image.crop((self.left, self.top, self.text_right, self.bottom))


def _bands(values: Sequence[int], threshold: float) -> List[Tuple[int, int]]:
    """Contiguous runs above ``threshold``, as ``(start, end)`` pairs."""
    out: List[Tuple[int, int]] = []
    start: Optional[int] = None
    for index, value in enumerate(values):
        if value > threshold and start is None:
            start = index
        elif value <= threshold and start is not None:
            if index - start >= MIN_BAND_HEIGHT:
                out.append((start, index))
            start = None
    if start is not None and len(values) - start >= MIN_BAND_HEIGHT:
        out.append((start, len(values)))
    return out


EDGE_TOLERANCE = 0.03


#: How unequal three columns may be before they are not three columns.
#:
#: The publisher prints boxes of one width, so a real page's columns match to within a few
#: pixels; 1.25 is loose enough that a missed edge does not disqualify a good page.
COLUMN_WIDTH_RATIO = 1.25


def _evenly_spread(edges: Sequence[Tuple[int, int]]) -> bool:
    """Whether these columns are the same width, as printed columns are.

    Only consulted where no dividers were found. That branch exists for a real page -- the last
    page of a part draws no photo panels, and requiring the divider discarded those pages whole,
    taking fourteen electors of part 5 with them -- but four rules anywhere will satisfy it, and
    on the closing summary page they do.

    At 400 dpi that page yields two vertical rules, the frame, and is correctly a summary. At
    the scan's native size it yields four, and this branch built three columns of 745, 196 and
    197 pixels with their dividers interpolated. The part then loses its closing total, which is
    the number every completeness check is measured against, and gains about six rows of nothing.

    A real page's columns are equal. That is the whole test.
    """
    widths = [right - left for left, right in edges]
    if not widths or min(widths) <= 0:
        return False
    return max(widths) / min(widths) <= COLUMN_WIDTH_RATIO


def column_triples(v_rules: Sequence[int]) -> List[Tuple[int, int, int]]:
    """Group vertical rules into ``(left, divider, right)`` per box column.

    The rules do not come in tidy threes. Adjacent boxes **share** a border, drawn as a tight
    cluster of two or three lines, so three columns produce seven clusters rather than nine
    rules::

        39 | 427 | 541 554 567 | 955 | 1069 1082 1094 | 1483 | 1597 1610
        L1   div1   R1    L2     div2   R2      L3      div3   R3  page

    Reading them as consecutive triples finds nothing. Reading the shared clusters as *both*
    the right edge of one box and the left edge of the next -- taking the outermost line on
    each side, so the gutter is excluded from both -- recovers the geometry exactly.

    **The last page of a part draws no dividers.** Where a part runs out of electors partway
    down a page, the publisher prints the boxes but not the photo panels, so that page has
    six vertical rules instead of twelve -- three columns of bare outer edges. Requiring the
    divider discarded those pages whole, and with them every elector on them: part 5 came out
    at 330 against a published 344, and the missing fourteen were all on the page that was
    thrown away. So a column may arrive either way, and where the divider was never printed
    its position is taken from the ratio the full pages establish.
    """
    rules = sorted(set(v_rules))
    if len(rules) < COLUMNS + 1:
        return []
    tolerance = max(4, (rules[-1] - rules[0]) * EDGE_TOLERANCE)

    clusters: List[List[int]] = [[rules[0]]]
    for rule in rules[1:]:
        if rule - clusters[-1][-1] <= tolerance:
            clusters[-1].append(rule)
        else:
            clusters.append([rule])

    if len(clusters) >= COLUMNS * 2 + 1:
        edges = [(clusters[i * 2][-1], clusters[i * 2 + 2][0]) for i in range(COLUMNS)]
        dividers = [clusters[i * 2 + 1][0] for i in range(COLUMNS)]
    elif len(clusters) == COLUMNS * 2:
        # The last column ran out of electors partway down, so its photo divider stops being
        # printed and the page yields six clusters instead of seven. Every edge is still there:
        #
        #     78 | 854 | 1082,1108,1133 | 1909 | 2137,2164,2189 | 3220
        #     L1   div1        R1/L2       div2       R2/L3        R3      (div3 never drawn)
        #
        # Read as three bare columns instead, part 3 page 23 was rejected outright and its
        # eleven electors -- serials 601 to 611, plainly printed -- were dropped with nothing
        # raised. The divider is derived from box width where it was never inked, which is what
        # the branch below already does for pages that print no dividers at all.
        edges = [
            (clusters[0][-1], clusters[2][0]),
            (clusters[2][-1], clusters[4][0]),
            (clusters[4][-1], clusters[5][0]),
        ]
        dividers = [clusters[1][0], clusters[3][0], None]
        if not _evenly_spread(edges):
            return []
    elif len(clusters) >= COLUMNS + 1:
        edges = [(clusters[i][-1], clusters[i + 1][0]) for i in range(COLUMNS)]
        dividers = [None] * COLUMNS
        if not _evenly_spread(edges):
            return []
    else:
        return []

    out: List[Tuple[int, int, int]] = []
    for (left, right), divider in zip(edges, dividers):
        if right <= left:
            return []
        if divider is None:
            divider = left + int((right - left) * TEXT_FRACTION)
        if not left < divider < right:
            return []
        out.append((left, divider, right))
    return out


def _mode(values: Sequence[int], tolerance: float) -> Optional[int]:
    """The value with the most neighbours within ``tolerance`` -- a mode that tolerates jitter."""
    if not values:
        return None
    best, support = None, -1
    for candidate in values:
        votes = sum(1 for value in values if abs(value - candidate) <= tolerance)
        if votes > support:
            best, support = candidate, votes
    return best


def row_bands(h_rules: Sequence[int]) -> List[Tuple[int, int]]:
    """The ten box rows, derived from the row pitch and snapped back onto real rules.

    Selecting bands by height alone does not survive contact with the pages. Some boxes carry
    an **internal** horizontal rule under the EPIC line, which splits one 416px row into a
    154px and a 260px span; the distribution of span heights is then bimodal (gutters ~25,
    rows ~416) with stragglers, and its median lands on nothing real -- an early version
    picked 154 and returned a single row for a page holding thirty electors.

    A row is then any pair of rules one row-height apart. Pairing rather than stepping from an
    anchor matters twice over: the first rule on a page is the page border, not the first row,
    so a predicted sequence starts 115px wrong and snaps to nothing; and pairing simply
    ignores an internal rule, because nothing sits one row-height below it.

    The height itself is **chosen by how much of the page it explains**, not by frequency. A
    mode fails outright: on pages where most boxes carry the internal rule, the spans it
    creates (156 and 262) outnumber the true 415, so the most common span is not a row.
    Scoring each candidate by the page area its pairing covers picks 415 by a factor of four,
    and is self-validating in a way counting never is.
    """
    rules = sorted(set(h_rules))
    if len(rules) < 3:
        return []
    spans = [b - a for a, b in zip(rules, rules[1:]) if b - a > 0]
    if not spans:
        return []

    gutter = _mode([s for s in spans if s <= max(spans) * 0.25], max(spans) * 0.02) or 0
    snap = max(4.0, gutter * 1.5)

    def pair(height: int) -> List[Tuple[int, int]]:
        out: List[Tuple[int, int]] = []
        for top in rules:
            bottom = min(rules, key=lambda r: abs(r - (top + height)))
            if abs(bottom - (top + height)) > snap or bottom <= top:
                continue
            # Rows are separated by a real gutter. Without this the *bottom* rule of one row
            # pairs with the *bottom* of the next -- 568+415 lands within tolerance of 1008 --
            # and every row after the first is offset by a gutter's width.
            if out and top - out[-1][1] < gutter * 0.5:
                continue
            out.append((top, bottom))
        return out[:ROWS]

    candidates = sorted({s for s in spans if s > gutter * 3}, reverse=True)
    best: List[Tuple[int, int]] = []
    best_score = (0, 0)
    for height in candidates:
        rows = pair(height)
        score = (sum(b - a for a, b in rows), len(rows))
        if score > best_score:
            best, best_score = rows, score
    return best


def build(h_rules: Sequence[int], v_rules: Sequence[int]) -> List[Box]:
    """Every elector box on the page, in reading order."""
    columns = column_triples(v_rules)
    rows = row_bands(h_rules)
    if not columns or not rows:
        return []
    return [
        Box(
            row=r,
            column=c,
            left=left,
            top=top,
            right=right,
            bottom=bottom,
            text_right=divider,
        )
        for r, (top, bottom) in enumerate(rows)
        for c, (left, divider, right) in enumerate(columns)
    ]


#: A text column with less of its area inked than this holds no elector. Measured: a filled
#: box runs 2-6% inked, a blank one under 0.2%.
BLANK_INK_FRACTION = 0.008


def has_ink(image: Image.Image, box: Box) -> bool:
    """Whether this box holds anything at all.

    Emptiness is decided by **ink, not by content**. Deciding it from whether any field
    parsed conflates the publisher leaving a box blank at the end of a part with OCR failing
    to read a box that is plainly full -- and silently drops the second, which is how every
    part came up a handful of electors short of its published total. A box with ink and no
    readable fields is a failure to record, not an absence to ignore.
    """
    crop = box.text_crop(image).convert("L")
    array = np.asarray(crop)
    if array.size == 0:
        return False
    skip = max(1, int(crop.height * BORDER_SKIP))
    body = array[skip:]
    if body.size == 0:
        return False
    return float((body < INK_LEVEL).sum()) / body.size > BLANK_INK_FRACTION


def text_bands(image: Image.Image, box: Box) -> List[Tuple[int, int]]:
    """The text lines inside one box, in page coordinates.

    Projects ink down the **text column only**, with the top border skipped. Both exclusions
    were necessary: with the photo column included the name line is lost to tesseract's
    column merging, and with the border included the EPIC line falls under the threshold.
    """
    crop = box.text_crop(image).convert("L")
    array = np.asarray(crop)
    skip = max(1, int(crop.height * BORDER_SKIP))
    body = array[skip:]
    if body.size == 0:
        return []
    ink = (body < INK_LEVEL).sum(axis=1)
    if not ink.size or ink.max() == 0:
        return []
    found = _bands(ink.tolist(), float(ink.max()) * INK_FRACTION)
    height = crop.height
    return [
        (
            box.top + max(0, skip + start - BAND_PAD),
            box.top + min(height, skip + end + BAND_PAD),
        )
        for start, end in found
    ]
