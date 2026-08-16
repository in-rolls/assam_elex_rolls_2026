"""The roll's own arithmetic, read off its closing page.

Every part ends with a page headed ``ভোটাৰৰ সংখ্যা`` -- "number of voters" -- carrying the
publisher's own breakdown of the list. Its main-list row reads::

    মূল তালিকা ... 453 420 873

and that is the number the main roll's rows should be counted against.

**This is not the same number as the info page's ``electors_total``.** That one is a *net*
struck after supplements: 850 for the part whose main list is 873. Comparing extracted rows
to the net left a residual of +4 to +27 per part that looked like an extraction defect and
was not -- parts 1, 4 and 5 already matched their main-list totals **exactly** while
appearing to be over by 23, −14 and +7 against the net.

I previously recorded that gap as "in the publisher's arithmetic ... not recoverable from
these PDFs". It is recoverable. It is printed on the last page of every part.

The line is **self-validating**: ``male + female == total``. That is what makes a retry safe,
and it is the same property that made the info-page elector table trustworthy -- a candidate
reading is accepted only if it balances, so a second attempt can never replace a right answer
with a plausible wrong one. ``assam_rolls.parse.balance_electors`` does exactly this for the
info pages; this is the same move on a different page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from PIL import Image

from assam_rolls import schema

#: The main-list row, in either language the roll is printed in. Assamese calls it ``মূল তালিকা``;
#: the English edition calls it ``Mother Roll ... Basic Roll of Special Revision``, and both repeat
#: the label in the row's own prose. The match is loose -- it only has to find the line, and the
#: arithmetic decides whether it is the right one.
#:
#: Without the English form, AC113's closing page was read and then discarded: 0 of 261 parts
#: carried a printed total, so nothing about that constituency could be checked at all.
MAIN_ROW = re.compile(r"মূল\s*তালিকা|MOTHER\s*ROLL|BASIC\s*ROLL", re.IGNORECASE)

#: A closing table can accidentally form the same three-column grid as elector boxes. Its title
#: is unambiguous and is read from the header band before any of those cells are emitted as rows.
SUMMARY_TITLE = re.compile(
    r"SUMMARY\s+OF\s+ELECTORS|ভোটাৰৰ\s+সংখ্যা|ভোটারের\s+সংখ্যা", re.IGNORECASE
)


def is_summary(text: str) -> bool:
    """Whether a page header explicitly identifies the closing elector table."""
    return bool(SUMMARY_TITLE.search(" ".join(text.split())))


#: Scale/segmentation pairs tried in order until the arithmetic balances. Scale does most of
#: the work -- the usual lesson here -- but this page is a wide sparse table rather than a
#: block of prose, so a couple of segmentation modes are worth trying once scale is exhausted.
ATTEMPTS: Tuple[Tuple[int, int], ...] = (
    (1, 6),
    (2, 6),
    (3, 6),
    (1, 4),
    (2, 4),
    (1, 11),
)

#: A part with fewer electors than this is not plausible, and a "total" that small is a
#: fragment of some other number on the page.
MIN_TOTAL = 20


@dataclass(frozen=True)
class RollSummary:
    """What the closing page says the part contains."""

    male: int
    female: int
    total: int
    scale: int
    third: int = 0

    @property
    def balances(self) -> bool:
        return self.male + self.female + self.third == self.total

    @property
    def male_share(self) -> Optional[float]:
        return (self.male / self.total) if self.total else None


def candidates(line: str) -> List[Tuple[int, int, int, int]]:
    """Every ``(male, female, third, total)`` the line could be stating.

    The third-gender column is **printed only when it is non-zero**, so the row is three
    numbers on most parts and four on some: part 2's reads ``315 302 1 618``, and reading it
    as a triple finds nothing because 315 + 302 is 617. Both shapes are tried and the
    arithmetic decides, which is the same invariant the info pages use --
    ``male + female + third == total``.

    The row also carries a year and a list number, so candidates are filtered by the only
    thing that distinguishes the real one: it adds up.
    """
    numbers = [int(n) for n in re.findall(r"\d+", schema.normalize_digits(line)) if len(n) <= 5]
    found: List[Tuple[int, int, int, int]] = []
    for index in range(len(numbers)):
        window = numbers[index : index + 4]
        if len(window) >= 3 and window[0] + window[1] == window[2] >= MIN_TOTAL:
            found.append((window[0], window[1], 0, window[2]))
        if len(window) == 4 and sum(window[:3]) == window[3] >= MIN_TOTAL:
            found.append((window[0], window[1], window[2], window[3]))
    return found


def parse(text: str) -> Optional[Tuple[int, int, int, int]]:
    """The main-list ``(male, female, total)`` from one OCR of the page, or ``None``.

    The **last** balancing triple on the line wins. The row's prose runs ahead of its
    figures -- ``মূল তালিকা মূল তালিকা বিশেষ সংশোধন, 2026 চনৰ পূৰ্বৰ 453 420 873`` -- so a
    year can precede the real numbers, and the figures are always at the end.
    """
    for line in text.split("\n"):
        if not MAIN_ROW.search(line):
            continue
        found = candidates(line)
        if found:
            return found[-1]
    return None


def read(
    engine,
    image: Image.Image,
    lang: str = "asm",
    attempts: Sequence[Tuple[int, int]] = ATTEMPTS,
) -> Optional[RollSummary]:
    """Read the closing page, retrying until the arithmetic holds.

    Returns ``None`` when nothing produces a balancing triple. That is deliberately not a
    fallback to the info page's net: a part whose own total could not be read should be
    reported as unmeasured rather than scored against a different quantity.
    """
    for scale, psm in attempts:
        found = parse(engine._run(image, lang, None, scale, psm=psm))
        if found:
            male, female, third, total = found
            summary = RollSummary(male=male, female=female, third=third, total=total, scale=scale)
            if summary.balances:
                return summary
    return None
