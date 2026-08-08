"""Which page is which, decided by what is ruled on it rather than by its number.

A part PDF is three kinds of page in sequence: the two info pages already parsed into
``dataset/parts.jsonl.gz``, then the elector pages, then a summary. Page *count* varies with
how many electors a part has, so only the first two positions are fixed and everything else
has to be recognised.

Recognising it by rule signature rather than by position is not fussiness. The info-page
pipeline was scoped as "ACs 64-73" from a one-page-per-AC sample and turned out to be 28 ACs
and 2,601 pages once every page was measured; layout in this corpus varies more than a
sample suggests. Only one AC of rolls is downloadable today, so any constant read off AC 1
is a guess about the other 125.

The signature is robust because it is structural:

    elector page   3 columns of boxes  ->  >= 9 vertical rules, >= 20 horizontal
    summary page   ruled rows, no columns  ->  0 vertical rules
    info page      the form grid           ->  a handful of each

An unrecognised page is **flagged, never parsed**. A page this module cannot classify is a
page whose geometry we do not understand, and guessing at 30 box positions on it would
produce 30 plausible rows of nonsense.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Sequence

from PIL import Image

from assam_rolls import layout

#: Pages 1 and 2 are the info pages, already extracted. Everything after is roll content.
INFO_PAGES = 2

#: An elector page carries three columns of boxes. Each column contributes an outer pair of
#: rules plus an internal divider, so nine is the floor for three columns being present.
MIN_COLUMN_RULES = 9

#: Ten box rows, each bounded above and below, minus tolerance for a short final page.
MIN_ROW_RULES = 8


class PageKind(str, Enum):
    INFO = "info"
    ELECTOR = "elector"
    SUMMARY = "summary"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PageSignature:
    """What the rule detector saw, kept so a misclassification can be diagnosed."""

    number: int
    kind: PageKind
    h_rules: Sequence[int]
    v_rules: Sequence[int]

    @property
    def is_elector(self) -> bool:
        return self.kind is PageKind.ELECTOR


def classify(image: Image.Image, number: int) -> PageSignature:
    """Decide what a rendered page is, from its rules alone."""
    h_rules = layout.detect_h_rules(image)
    v_rules = layout.detect_v_rules(image, 0, image.height - 1) if h_rules else []

    if number <= INFO_PAGES:
        kind = PageKind.INFO
    elif len(v_rules) >= MIN_COLUMN_RULES and len(h_rules) >= MIN_ROW_RULES:
        kind = PageKind.ELECTOR
    elif not v_rules and h_rules:
        # Ruled rows with no columns: the closing summary of net electors.
        kind = PageKind.SUMMARY
    else:
        kind = PageKind.UNKNOWN

    return PageSignature(number=number, kind=kind, h_rules=tuple(h_rules), v_rules=tuple(v_rules))


def elector_pages(signatures: Sequence[PageSignature]) -> List[PageSignature]:
    return [s for s in signatures if s.is_elector]


def unknown_pages(signatures: Sequence[PageSignature]) -> List[int]:
    """Page numbers that could not be classified -- the thing to look at, not to parse."""
    return [s.number for s in signatures if s.kind is PageKind.UNKNOWN]
