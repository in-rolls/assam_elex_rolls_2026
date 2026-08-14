"""How many rows are provably wrong, without anyone reading a page.

Every check in :mod:`electors.reconcile` compares counts. None of them reads a name, so a
constituency can pass all of them with every name misread -- and thirteen million rows currently
carry no accuracy measurement of any kind.

The instinct is to hand-mark more boxes. That is the expensive route and it measures a hundred and
fifty rows. A large class of errors is **provably wrong from the data alone**: not "looks
suspicious", but wrong in a way that needs no ground truth to establish. A name in Latin letters on
a roll printed in Assamese is a misread whatever the page says. A person is not their own father.
An identifier that is unique by definition cannot appear twice.

Those give a **lower bound** on the error rate across every row, which is worth more than a point
estimate on a sample -- and it costs a pass over the parquet.

**A lower bound is not the error rate.** A row no detector condemns is unmeasured, not correct.
The bound and the hand-marked sample answer different questions and neither replaces the other:
this one says "at least this many are wrong", the sample says "about this many are right, give or
take". Only the sample can see a name that is plausible and wrong.

**Reported per language.** Assam publishes Assamese and Bengali editions and they take different
label paths through the parser. A single overall number showed the Bengali house-number column --
empty on every row of ten constituencies, 2.2 million of them -- as a two percent blemish.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Sequence

#: Latin letters and Latin digits. On a roll printed in Assamese or Bengali these do not belong in
#: a person's name: the engine mapped a Bengali glyph onto a lookalike from the wrong script.
LATIN = re.compile(r"[A-Za-z]")

#: Printed field labels. A label inside a *value* means the split between label and value failed,
#: so the value carries text that is not the person's.
LABELS = ("নাম", "বয়স", "লিঙ্গ", "ঘৰ", "বাড়ী", "বাড়ি", "নং")

#: Three letters and seven digits, as the roll prints it.
EPIC = re.compile(r"^[A-Z]{3}[0-9]{7}$")

MIN_AGE, MAX_AGE = 18, 120


@dataclass
class Finding:
    """One detector's verdict over a set of rows."""

    name: str
    condemned: int
    total: int
    why: str
    examples: List[str] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return self.condemned / self.total if self.total else 0.0


def _scan(
    rows: Sequence[Dict[str, Any]],
    name: str,
    why: str,
    test: Callable[[Dict[str, Any]], bool],
    show: Callable[[Dict[str, Any]], str],
) -> Finding:
    hits = [r for r in rows if test(r)]
    return Finding(name, len(hits), len(rows), why, [show(r) for r in hits[:4]])


def latin_in_name(rows: Sequence[Dict[str, Any]]) -> Finding:
    """A name containing Latin letters, on a roll printed in Bengali script."""
    return _scan(
        rows,
        "latin in name",
        "the roll prints names in Assamese/Bengali; Latin letters are a cross-script misread",
        lambda r: bool(LATIN.search(r.get("name") or "")),
        lambda r: f"{r.get('name')!r}",
    )


def name_is_relation(rows: Sequence[Dict[str, Any]]) -> Finding:
    """The elector's name and their relation's name identical -- one line read twice.

    This is the shape a swapped or duplicated band produces, and it is the failure a whitespace
    tolerant label matcher once *created*: it filled the relation field with the elector's own
    name, and every fill-rate metric improved.
    """
    return _scan(
        rows,
        "name equals relation",
        "one line read into two fields; a person is not their own parent or spouse",
        lambda r: bool(r.get("name")) and r.get("name") == r.get("relation_name"),
        lambda r: f"{r.get('name')!r}",
    )


#: A label counts as leaked only at one end of the value, as a word in its own right.
#:
#: Substring matching condemned ordinary names: ``বাইনাম`` contains ``নাম``, ``নংগা`` contains
#: ``নং``, ``বাড়িয়ৰ মুৰ্ম`` contains ``বাড়ি``. It reported 1.2% of rows where the true figure is
#: 0.58% -- half of what it flagged were people. A detector that condemns correct rows is worse
#: than no detector, because a floor is only a floor if everything under it is genuinely wrong.
#:
#: ``pages.py`` had already learned this -- it matches section markers as whole words because
#: ``যোগ`` sits inside the village name ``যোগেন্দ্ৰপুৰ`` -- and the lesson did not travel here.
#:
#: At either end, never in the middle. A failed split leaves the label in front of the value or
#: trailing behind it -- ``ঘৰ নং``, ``নাম লেকশী নাৰ্জাৰী``, ``নাউধা নাম`` -- while a label in the
#: middle of a name is part of the name. Both ends are checked because the trailing form is rarer
#: (73 rows against 11,362) but no less wrong.
_LABELS = "|".join(LABELS)
LEAKED = re.compile(rf"^\s*(?:{_LABELS})(?![ঀ-৿])|(?<![ঀ-৿])(?:{_LABELS})\s*$")


def label_in_value(rows: Sequence[Dict[str, Any]]) -> Finding:
    """A printed label at one end of a value, meaning the label/value split failed."""
    return _scan(
        rows,
        "label inside a value",
        "a printed label leaked past the label/value split",
        lambda r: any(LEAKED.search(r.get(f) or "") for f in ("name", "relation_name")),
        lambda r: f"{r.get('name')!r} / {r.get('relation_name')!r}",
    )


def malformed_epic(rows: Sequence[Dict[str, Any]]) -> Finding:
    """An identifier that does not match the format the roll prints."""
    return _scan(
        rows,
        "malformed EPIC",
        "the printed format is three letters and seven digits",
        lambda r: bool(r.get("epic_no")) and not EPIC.match(r["epic_no"]),
        lambda r: f"{r.get('epic_no')!r}",
    )


def repeated_epic(rows: Sequence[Dict[str, Any]]) -> Finding:
    """The same identifier on more than one row of one constituency.

    The most informative detector here and the cheapest. An EPIC is unique by construction, so a
    repeat means at least one of the rows sharing it is wrong -- no ground truth required. It is
    also *directional*: rows sharing an EPIC were already found to be eleven times likelier to
    carry an unreadable name, which is what implicated the digit-position repair.

    Counted as ``rows involved in a repeat`` rather than ``distinct repeated values``, because the
    question is how many rows are suspect, and only one row of each colliding pair is necessarily
    wrong -- so this over-counts by design and is a bound on the bound.
    """
    seen = collections.Counter(r["epic_no"] for r in rows if EPIC.match(r.get("epic_no") or ""))
    repeats = {value for value, n in seen.items() if n > 1}
    hits = [r for r in rows if r.get("epic_no") in repeats]
    return Finding(
        "repeated EPIC",
        len(hits),
        len(rows),
        "an identifier unique by definition, appearing twice; at least one row is wrong",
        [f"{v} x{seen[v]}" for v in list(repeats)[:4]],
    )


def impossible_age(rows: Sequence[Dict[str, Any]]) -> Finding:
    """An age outside what an electoral roll can contain."""
    return _scan(
        rows,
        "impossible age",
        f"an electoral roll holds nobody under {MIN_AGE} or over {MAX_AGE}",
        lambda r: r.get("age") is not None and not (MIN_AGE <= r["age"] <= MAX_AGE),
        lambda r: f"age {r.get('age')}",
    )


DETECTORS = (
    latin_in_name,
    name_is_relation,
    label_in_value,
    malformed_epic,
    repeated_epic,
    impossible_age,
)


def scan(rows: Sequence[Dict[str, Any]]) -> List[Finding]:
    return [detector(rows) for detector in DETECTORS]


def render(findings: Sequence[Finding], label: str = "") -> str:
    """The bound, with each detector's contribution named."""
    lines = [f"   {label}" if label else ""]
    total = findings[0].total if findings else 0
    lines.append(f"   {total:,} rows")
    for found in findings:
        mark = " " if found.condemned == 0 else "!"
        lines.append(f"     {mark} {found.name:<22} {found.condemned:>8,}  {found.rate:>6.2%}")
        if found.examples:
            lines.append(f"         e.g. {', '.join(found.examples[:3])}")
    return "\n".join(line for line in lines if line)
