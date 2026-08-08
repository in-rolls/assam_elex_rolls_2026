"""Turning one elector box into one elector.

A box holds six things in two places. The **header strip**, spanning the full width above the
text, carries the serial number at the left and the EPIC at the right. The **text column**
below it carries four labelled lines::

    নাম : খাদৰাম ৰাভা              name
    পিতাৰ নাম : গংগাৰাম ৰাভা       relation, and its type from the label
    ঘৰ নং : 21                     house number
    বয়স : 46 লিঙ্গ : পুৰুষ          age and sex

Three things measured here decide how it is read.

**The EPIC needs the English model, not the Assamese one.** ``asm`` renders ``HHK0001471`` as
``1414140001471`` -- it is trained on a script that has no Latin letters, so it maps them onto
digits that look similar. Read with ``eng`` the same crop comes back exactly right. The EPIC
is the only globally unique field in the dataset, so this one substitution is worth more than
any other tuning here.

**Bands are anchored on the age line, then ordered.** Matching every line by its label was
tried first and failed on the house number: ``ঘৰ নং`` comes back as ``ছাৰ ন`` or ``হৰ ক্খত``,
so it matched nothing and every house number came out empty. The last line anchors the box
instead, and the publisher's fixed order supplies the rest.

Finding *that* line is what the sex statistics turned on. Anchoring on ``বয়স`` or a plausible
age left five boxes on one page with no age line at all, and **all five were male** -- their
line read ``বৈবলমলস ' 2/ লঙ্গ ' পৰম``, label garbled and digits unreadable, so age and sex were
both lost. That, not the sex matcher, was the 44.2% male share against the roll's own 50.8%.
The ``লিঙ্গ`` label is the durable thing on that line, and it is searched for from the end.

**The serial number is derived, not read.** It is small, sits alone in a wide strip, and OCR
returns it for barely half the boxes even at three scales. But serials run consecutively
through a part, so position determines it and OCR only *confirms* it -- the same move the
info pipeline made with the elector arithmetic, where a checkable relation beat a better
guess. A disagreement is recorded rather than resolved silently.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image

from assam_rolls import schema

from . import batch, grid

#: ``HHK0001471``. Three letters and seven digits is the modern format; the bounds are loose
#: because older cards and OCR both stray.
EPIC_RE = re.compile(r"[A-Z]{2,4}\d{6,9}")

#: Read at these upscales and compared. Scale, not page-segmentation mode, decides a
#: marginal read -- measured on the info pages.
#:
#: Native resolution was tried and rejected. Counting how many crops still contained the
#: *label* said scale 1 was as good as scale 2 for half the cost (51 vs 52 lines with ``নাম``)
#: -- but that measured the wrong thing. Extracting the *values* on a whole page, scale 1 cost
#: age 77% -> 63% and sex 83% -> 73%: the printed label survives a coarse read, the
#: handwritten-looking digits beside it do not. Speed is not worth a seventh of the ages.
NAME_SCALES: Tuple[int, ...] = (2, 3)

#: The header holds the EPIC in small Latin type, which does gain from an upscale. One pass:
#: the two scales agreed on almost every box, so the second bought nothing per page.
HEADER_SCALES: Tuple[int, ...] = (2,)

#: Label -> field. Longest first, because ``পিতাৰ নাম`` contains ``নাম``.
LABELS: Sequence[Tuple[str, str]] = (
    ("পিতাৰ নাম", "father"),
    ("পিতৰ নাম", "father"),
    ("পতাৰ নাম", "father"),
    ("স্বামীৰ নাম", "husband"),
    ("স্বামৰ নাম", "husband"),
    ("স্বামাৰ নাম", "husband"),
    ("মাতাৰ নাম", "mother"),
    ("মাতৃৰ নাম", "mother"),
)

HOUSE_LABELS = ("ঘৰ নং", "ঘৰনং", "ঘর নং", "ঘৰ ন")
AGE_LABEL = "বয়স"
NAME_LABEL = "নাম"

#: ``লিঙ্গ`` ("sex"), however badly it scanned. It survives as লঙ্গ, ল্গ, লল্গী, ললক -- always a
#: ``ল`` and a ``গ``-like letter within a few characters -- and it is the most durable thing on
#: the age line. ``বয়স`` is not: it comes back as বৈবলমলস, ়বৈলযস, ৈলৰযস.
SEX_LABEL = re.compile(r"ল[\u0980-\u09FF]{0,3}[গকঙ]")

#: Anything outside this is not a voter age and is recorded as missing.
MIN_AGE, MAX_AGE = 18, 120


@dataclass
class Elector:
    """One row of the output. ``flags`` says what could not be established."""

    serial_no: Optional[int] = None
    serial_no_ocr: Optional[int] = None
    epic_no: str = ""
    name: str = ""
    relation_name: str = ""
    relation_type: str = ""
    house_no: str = ""
    age: Optional[int] = None
    sex: str = ""
    flags: List[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """A box the publisher left blank at the end of a part -- not a failure to read.

        Every field counts, not just the three most reliable ones. Testing only EPIC, name
        and age discarded boxes that had read a relation and a sex but nothing else, which
        is how the first run came up nine electors short of the published total.
        """
        return not any(
            (self.epic_no, self.name, self.age, self.relation_name, self.house_no, self.sex)
        )

    @property
    def needs_review(self) -> bool:
        return bool(self.flags)


#: Punctuation and table furniture OCR picks up around a value.
EDGE_PUNCTUATION = " :;.,'\"-\u2018\u2019"

#: Vowel signs and the virama. A *leading* one is scanner debris -- no Assamese word begins
#: with a matra. A *trailing* one is usually the word: ``ৰাভা``, ``শৰ্মা`` and ``বৰুৱা`` all end
#: in one, and stripping it silently truncated almost every surname on the page.
LEADING_MARKS = "\u09bc\u09be\u09bf\u09c0\u09c1\u09c2\u09c7\u09c8\u09cb\u09cc\u09cd"


def _clean(text: str) -> str:
    """Strip the debris around a value, repeatedly.

    Repeatedly, because the two kinds interleave: a real read of ``ু ' বাসন্তা ৰাভা`` has a
    stray matra *then* a quote, and a single pass in either order leaves the other behind.
    """
    out = re.sub(r"[|\[\]{}_~`^*=<>]+", " ", text)
    out = re.sub(r"\s+", " ", out).strip()
    while True:
        stripped = out.strip(EDGE_PUNCTUATION).lstrip(LEADING_MARKS + " ")
        if stripped == out:
            return stripped
        out = stripped


def value_after(line: str, label: str) -> str:
    """The text after ``label`` and its separator, or ``""`` when the label is absent."""
    index = line.find(label)
    if index < 0:
        return ""
    rest = line[index + len(label) :]
    return _clean(rest.lstrip(" :;'‘’"))


def house_number(line: str) -> str:
    """The house number, taken as what follows the label or -- failing that -- the digits.

    This line scans worse than any other in the box, so the label is often unrecoverable.
    Falling back to the digits it contains is imperfect (some house numbers are not numeric)
    but recovers a value where insisting on the label recovers nothing.
    """
    for label in HOUSE_LABELS:
        if label in line:
            value = schema.normalize_digits(value_after(line, label))
            if value:
                return value
    digits = re.findall(r"[0-9]+(?:[-/][0-9]+)*", schema.normalize_digits(line))
    return digits[0] if digits else ""


def relation_of(line: str) -> Tuple[str, str]:
    """``(relation_name, relation_type)`` from a relation line."""
    for label, kind in LABELS:
        if label in line:
            return value_after(line, label), kind
    return "", ""


def age_from(line: str) -> Optional[int]:
    """The first plausible age in a line, or ``None``.

    ``বয়স : 55`` often comes back as ``525`` or ``5 5``: the Assamese model splits and doubles
    digits it is not confident about. So candidates are taken both from the digit runs and
    from their pairs, and the first that could be a voter's age wins.
    """
    normalized = schema.normalize_digits(line)
    runs = re.findall(r"\d+", normalized)
    candidates: List[str] = []
    for run in runs:
        candidates.append(run)
        # A three-digit run in this field is nearly always two digits with one doubled.
        if len(run) == 3:
            candidates.extend((run[:2], run[1:]))
    joined = "".join(runs)
    if len(joined) >= 2:
        candidates.append(joined[:2])
    for candidate in candidates:
        value = int(candidate)
        if MIN_AGE <= value <= MAX_AGE:
            return value
    return None


def age_and_sex(line: str) -> Tuple[Optional[int], str]:
    """Age and sex from the ``বয়স ... লিঙ্গ ...`` line."""
    age = age_from(line)
    return age, sex_of(line)


#: The three words the sex field can hold, and the code each maps to.
SEX_WORDS: Sequence[Tuple[str, str]] = (("পুৰুষ", "M"), ("মহিলা", "F"), ("তৃতীয়", "T"))

#: How closely a damaged read must resemble one of them to count. Below this the sex is
#: recorded as unknown rather than guessed.
SEX_SIMILARITY = 0.5


def sex_of(line: str) -> str:
    """The sex, scored against all three words rather than matched against a fragment list.

    An ordered list of fragments was tried first and **biased the dataset**. ``মহিলা`` is
    longer and more distinctive than ``পুৰুষ``, so it survived damage that destroyed the male
    word, and the female fragments sat earlier in the list. Against a published 51% male /
    49% female, the fragment matcher returned **41% / 59%** -- and it did so on rows whose age
    had read perfectly, so this was the matcher, not the scan.

    Scoring every candidate and taking the best is symmetric by construction: neither word can
    win by being listed first, and neither loses by being shorter.
    """
    tail = re.sub(r"[^ঀ-৿]", "", line)[-12:]
    # Two characters is the shortest window scored below. A one-character tail made
    # ``range(2, 2)`` empty and ``max()`` raise, which killed the whole part rather than the
    # one box -- found by a real run dying on it, not by reading the code.
    if len(tail) < 2:
        return ""
    # The word sits at the end but its length varies with how the scan mangled it, and a
    # fixed window dilutes the comparison -- ``পৰম`` against a 12-character tail scores below
    # threshold even though it is unmistakably পুৰুষ. Scoring every plausible suffix and
    # keeping the best makes the window fit the word rather than the other way round.
    best, score = "", 0.0
    for word, code in SEX_WORDS:
        ratio = max(
            difflib.SequenceMatcher(None, tail[-length:], word).ratio()
            for length in range(2, len(tail) + 1)
        )
        if ratio > score:
            best, score = code, ratio
    return best if score >= SEX_SIMILARITY else ""


def _is_age_line(line: str) -> bool:
    """Whether this band is the age/sex line.

    Recognising it mattered more than it looked. Requiring ``বয়স`` or a plausible age left
    five boxes on one page with no age line at all -- and **all five were male**, because
    their line read ``বৈবলমলস ' 2/ লঙ্গ ' পৰম``: the label garbled and the digits unreadable,
    so neither test fired and both the age *and* the sex were lost. That is the whole of the
    44.2% male share against the roll's own 50.8%; it was never the sex matcher.

    So the ``লিঙ্গ`` label counts too. It is the most durable thing on the line.
    """
    if AGE_LABEL in line or SEX_LABEL.search(line):
        return True
    digits = re.findall(r"\d{1,3}", schema.normalize_digits(line))
    return any(MIN_AGE <= int(d) <= MAX_AGE for d in digits) and bool(sex_of(line))


def assign_bands(lines: Sequence[str]) -> Dict[str, str]:
    """Map OCR'd lines onto fields, anchored on the age line and ordered from there.

    Labels alone are not enough. ``ঘৰ নং`` comes back as ``ছাৰ ন`` or ``হৰ ক্খত`` -- the house
    line is the worst-scanned in the box -- so label matching found it in none of the first
    six boxes tried, and every house number came out empty.

    The age line is the opposite: ``বয়স`` and a two-digit number survive almost any scan, so
    it anchors the box. The publisher's order is fixed -- name, relation, house, age -- so
    once the last line is known the rest follow by position, with labels used to confirm or
    correct rather than to find.
    """
    lines = [line for line in lines if line]
    if not lines:
        return {}
    out: Dict[str, str] = {}

    # Searched from the **end**: the age line is always last, and a widened test can match a
    # name that happens to contain the same letters. Taking the last match cannot be fooled
    # by a line above it.
    age_index = next((i for i in range(len(lines) - 1, -1, -1) if _is_age_line(lines[i])), None)
    if age_index is not None:
        out["age"] = lines[age_index]
        above = lines[:age_index]
    else:
        above = lines

    # Order is name, relation, house. Take them from the end so a missing first line pushes
    # the others up rather than shifting everything down.
    for offset, key in enumerate(("house", "relation", "name")):
        if len(above) > offset:
            out[key] = above[len(above) - 1 - offset]

    # A label, where it survived, outranks position.
    for line in lines:
        if any(label in line for label, _ in LABELS):
            out["relation"] = line
        elif NAME_LABEL in line and "relation" in out and out["relation"] != line:
            out.setdefault("name", line)
    return out


def consensus(readings: Sequence[str]) -> Tuple[str, bool]:
    """The agreed reading and whether the scales agreed.

    Agreement is the signal worth keeping. Where two independent upscales produce the same
    string the value is almost certainly right; where they differ the longer one is kept and
    the disagreement is flagged, because silently picking one is how a plausible wrong name
    gets published as fact.
    """
    cleaned = [r for r in readings if r]
    if not cleaned:
        return "", True
    if len(set(cleaned)) == 1:
        return cleaned[0], True
    return max(cleaned, key=len), False


def read_page(image: Image.Image, boxes: Sequence[grid.Box]) -> List[Tuple[grid.Box, "Elector"]]:
    """Every elector on one page, in three tesseract invocations instead of three hundred.

    Reading box by box cost ten process spawns per elector. Here the whole page's crops go in
    one batch each: the text lines, then the name line again at a second scale for the
    consensus, then the headers in the English model. Everything else is unchanged -- the same
    crops, the same models -- so this is purely the cost of asking, not of the answer.

    Boxes without ink are skipped before any OCR, since a blank box at the end of a part is
    the publisher's doing and there is nothing in it to read.
    """
    live = [box for box in boxes if grid.has_ink(image, box)]
    if not live:
        return []

    bands = {box: grid.text_bands(image, box) for box in live}
    crops: List[Image.Image] = []
    spans: List[Tuple[grid.Box, int]] = []
    for box in live:
        for top, bottom in bands[box]:
            crops.append(image.crop((box.left, top, box.text_right, bottom)))
            spans.append((box, len(crops) - 1))

    first_scale = batch.texts(crops, lang="asm", psm=7, scale=NAME_SCALES[0])

    # The second scale is for the *name* consensus, so only the name band pays for it.
    # Running it over all four bands cost three-quarters of the page's OCR time for a
    # second opinion on the house number, which nothing uses.
    name_index = {}
    for box in live:
        first = next((i for b, i in spans if b is box), None)
        if first is not None:
            name_index[box] = first
    name_crops = [crops[name_index[box]] for box in live if box in name_index]
    name_second = batch.texts(name_crops, lang="asm", psm=7, scale=NAME_SCALES[1])
    second_by_box = {
        box: name_second[position]
        for position, box in enumerate(box for box in live if box in name_index)
    }

    # The EPIC sits **right of the photo divider** and the serial at the far left, with a
    # thousand pixels of whitespace between them. Read as one strip, psm 7 gives up: on an
    # untouched part it matched 24 of 30, and six of the misses came back completely empty
    # while their crops measured a normal 92-94px, so geometry was never the problem. Read as
    # its own zone the same page matches **30 of 30**, at every scale and segmentation mode
    # tried -- a fix that works at only one setting is usually a coincidence.
    strips = [
        (box, box.top + 4, max(box.top + 12, bands[box][0][0] if bands[box] else box.top + 110))
        for box in live
    ]
    epic_reads = batch.texts(
        [image.crop((b.text_right, y0, b.right, y1)) for b, y0, y1 in strips],
        lang="eng",
        psm=7,
        scale=HEADER_SCALES[0],
    )
    serial_reads = batch.texts(
        [image.crop((b.left, y0, b.text_right, y1)) for b, y0, y1 in strips],
        lang="eng",
        psm=7,
        scale=HEADER_SCALES[0],
        whitelist="0123456789",
    )

    lines_by_box: Dict[grid.Box, Tuple[List[str], List[str]]] = {box: ([], []) for box in live}
    for box, index in spans:
        lines_by_box[box][0].append(first_scale[index])
    for box in live:
        if box in second_by_box:
            lines_by_box[box][1].append(second_by_box[box])

    out: List[Tuple[grid.Box, Elector]] = []
    for position, box in enumerate(live):
        out.append(
            (box, _assemble(lines_by_box[box], epic_reads[position], serial_reads[position]))
        )
    return out


#: Stripped before the EPIC is matched. A real read came back ``S 106 -, HHK3535/704``: the
#: EPIC is plainly there and a stray slash inside the digits was enough to reject it.
NON_ALNUM = re.compile(r"[^A-Za-z0-9]")


def _assemble(lines: Tuple[List[str], List[str]], epic_read: str, serial_read: str) -> Elector:
    """Build one elector from its text lines, its EPIC strip and its serial strip."""
    assigned = [assign_bands(scale_lines) for scale_lines in lines]
    elector = Elector()

    elector.name, agreed = consensus([value_after(a.get("name", ""), NAME_LABEL) for a in assigned])
    if not agreed:
        elector.flags.append("name_disagreement")

    relations = [relation_of(a.get("relation", "")) for a in assigned]
    elector.relation_name, agreed = consensus([name for name, _ in relations])
    elector.relation_type = next((kind for _, kind in relations if kind), "")
    if not agreed:
        elector.flags.append("relation_disagreement")

    elector.house_no = house_number(
        next((a.get("house", "") for a in assigned if a.get("house")), "")
    )
    for line in (a.get("age", "") for a in assigned if a.get("age")):
        age, sex = age_and_sex(line)
        elector.age = elector.age if elector.age is not None else age
        elector.sex = elector.sex or sex

    match = EPIC_RE.search(NON_ALNUM.sub("", epic_read).upper())
    elector.epic_no = match.group() if match else ""

    found = re.findall(r"\d{1,4}", schema.normalize_digits(serial_read))
    if found:
        elector.serial_no_ocr = int(found[0])

    if not elector.is_empty:
        for value, flag in (
            (elector.epic_no, "no_epic"),
            (elector.name, "no_name"),
            (elector.age, "no_age"),
            (elector.sex, "no_sex"),
        ):
            if not value:
                elector.flags.append(flag)
    return elector
