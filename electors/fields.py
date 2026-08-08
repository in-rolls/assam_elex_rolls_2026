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
so it matched nothing and every house number came out empty. The age line survives almost any
scan -- ``বয়স`` plus a two-digit number -- so it anchors the box, and the publisher's fixed
order supplies the rest. Labels then confirm or correct rather than find.

**The serial number is derived, not read.** It is small, sits alone in a wide strip, and OCR
returns it for barely half the boxes even at three scales. But serials run consecutively
through a part, so position determines it and OCR only *confirms* it -- the same move the
info pipeline made with the elector arithmetic, where a checkable relation beat a better
guess. A disagreement is recorded rather than resolved silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image

from assam_rolls import schema

from . import grid

#: ``HHK0001471``. Three letters and seven digits is the modern format; the bounds are loose
#: because older cards and OCR both stray.
EPIC_RE = re.compile(r"[A-Z]{2,4}\d{6,9}")

#: Read at these upscales and compared. Scale, not page-segmentation mode, decides a
#: marginal read -- measured on the info pages, and unchanged here.
NAME_SCALES: Tuple[int, ...] = (2, 3)
HEADER_SCALES: Tuple[int, ...] = (2, 3)

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

#: Matched as fragments, not whole words. ``পুৰুষ`` comes back as ``পৰষ``, ``পুৰম``, ``পুৰুষ``
#: depending on the scan, and a whole-word table misses every damaged one -- which showed up
#: immediately as a missing sex on boxes whose age read perfectly.
SEX_FRAGMENTS: Sequence[Tuple[str, str]] = (
    ("মহিলা", "F"),
    ("মাহলা", "F"),
    ("হিলা", "F"),
    ("মহল", "F"),
    ("মাহ", "F"),
    ("তৃতীয়", "T"),
    ("তৃতা", "T"),
    ("পুৰুষ", "M"),
    ("পুরুষ", "M"),
    ("পৰষ", "M"),
    ("পুৰ", "M"),
    ("ৰুষ", "M"),
    ("পৰম", "M"),
)

HOUSE_LABELS = ("ঘৰ নং", "ঘৰনং", "ঘর নং", "ঘৰ ন")
AGE_LABEL = "বয়স"
NAME_LABEL = "নাম"

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
    sex = ""
    for fragment, code in SEX_FRAGMENTS:
        if fragment in line:
            sex = code
            break
    return age, sex


def _is_age_line(line: str) -> bool:
    if AGE_LABEL in line:
        return True
    digits = re.findall(r"\d{1,3}", schema.normalize_digits(line))
    return any(MIN_AGE <= int(d) <= MAX_AGE for d in digits) and any(
        fragment in line for fragment, _ in SEX_FRAGMENTS
    )


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

    age_index = next((i for i, line in enumerate(lines) if _is_age_line(line)), None)
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


def read_header(
    engine, image: Image.Image, box: grid.Box, band_top: int
) -> Tuple[str, Optional[int]]:
    """EPIC and the OCR'd serial from the strip above the text.

    Read with ``eng``: the strip is Latin and digits, and the Assamese model turns
    ``HHK0001471`` into ``1414140001471``.
    """
    top = box.top + 4
    bottom = max(top + 8, band_top)
    reads = [
        engine._run(image.crop((box.left, top, box.right, bottom)), "eng", None, scale, psm=7)
        for scale in HEADER_SCALES
    ]
    epics = [m.group() for m in (EPIC_RE.search(r.replace(" ", "").upper()) for r in reads) if m]
    epic, _ = consensus(epics)

    serial = None
    for raw in reads:
        head = raw.split(epic)[0] if epic and epic in raw.replace(" ", "").upper() else raw
        found = re.findall(r"\d{1,4}", schema.normalize_digits(head))
        if found:
            serial = int(found[0])
            break
    return epic, serial


def read_box(engine, image: Image.Image, box: grid.Box) -> Elector:
    """Everything readable in one box."""
    bands = grid.text_bands(image, box)
    if not bands:
        return Elector(flags=["no_text_bands"])

    lines_by_scale: List[List[str]] = []
    for scale in NAME_SCALES:
        lines_by_scale.append(
            [
                engine._run(
                    image.crop((box.left, top, box.text_right, bottom)), "asm", None, scale, psm=7
                )
                for top, bottom in bands
            ]
        )

    assigned = [assign_bands(lines) for lines in lines_by_scale]
    elector = Elector()

    name_reads = [value_after(a.get("name", ""), NAME_LABEL) for a in assigned]
    elector.name, agreed_name = consensus(name_reads)
    if not agreed_name:
        elector.flags.append("name_disagreement")

    relation_reads = [relation_of(a.get("relation", "")) for a in assigned]
    elector.relation_name, agreed_relation = consensus([r for r, _ in relation_reads])
    elector.relation_type = next((k for _, k in relation_reads if k), "")
    if not agreed_relation:
        elector.flags.append("relation_disagreement")

    house_line = next((a.get("house", "") for a in assigned if a.get("house")), "")
    elector.house_no = house_number(house_line)

    # Every scale gets a vote on age and sex: the line is the same one, but a digit the
    # first scale doubled the second often reads cleanly.
    age_lines = [a.get("age", "") for a in assigned if a.get("age")]
    for line in age_lines:
        age, sex = age_and_sex(line)
        elector.age = elector.age if elector.age is not None else age
        elector.sex = elector.sex or sex

    elector.epic_no, elector.serial_no_ocr = read_header(engine, image, box, bands[0][0])

    if not elector.is_empty:
        if not elector.epic_no:
            elector.flags.append("no_epic")
        if not elector.name:
            elector.flags.append("no_name")
        if elector.age is None:
            elector.flags.append("no_age")
        if not elector.sex:
            elector.flags.append("no_sex")
    return elector
