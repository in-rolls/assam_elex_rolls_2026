"""The one rule that must not drift: transliterate, never translate.

``গোসাইগাও ৰাজহ চক্ৰ`` becomes *Gossaigaon Rajah Chakra*, not *Gossaigaon Revenue Circle*.
Both are defensible English renderings; only the first is transliteration, and a table
filled partly by machine and partly by hand is exactly where the two quietly mix.

The risk is concrete. These generic terms recur as suffixes, and each has an obvious
English translation a helpful editor might reach for::

    পৌৰসভা      paurasabha    not "Municipality"        ends 63 distinct values
    (অংশ-১)     (ansh-1)      not "(Part-1)"            ends 48
    থানা        thana         not "Police Station"      ends 34
    উপ ডাকঘৰ    up dakghar    not "Sub Post Office"     ends 33
    ৰাজহ চক্ৰ    rajah chakra  not "Revenue Circle"      ends 27

**The check has to look at both sides.** An earlier version banned the English words
outright and immediately produced a false positive on ``লামডিং ৰেলৱে টাউন`` ->
``lumding relway town``: টাউন *is* the English "town" borrowed into Assamese and written in
Bengali script, so "town" there is the faithful transliteration, not a translation. The
corpus is full of such loanwords -- ৰেলৱে (railway), কলেজ (college), ৰোড (road).

So a violation requires **both**: the English word in the output *and* the native word it
would be the translation of in the input. If the source says টাউন, "town" is right; only if
the source says চহৰ is "town" a translation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Tuple


@dataclass(frozen=True)
class Rule:
    """One way a translation could slip in."""

    english: str
    #: Native forms that mean this and would have to be *translated* to produce it.
    natives: Tuple[str, ...]
    #: What the transliteration should be instead.
    expected: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "_pattern", re.compile(rf"\b{re.escape(self.english)}\b", re.I))

    def violated_by(self, native: str, roman: str) -> bool:
        if not roman or not self._pattern.search(roman):  # type: ignore[attr-defined]
            return False
        return any(form in native for form in self.natives)


RULES: Tuple[Rule, ...] = (
    Rule("police station", ("থানা", "পুলিশ থানা", "পুলিচ থানা"), "thana"),
    Rule("revenue circle", ("ৰাজহ চক্ৰ", "রাজস্ব চক্র", "ৰাজহ চক্র"), "rajah chakra"),
    Rule("municipality", ("পৌৰসভা", "পোৰসভা", "পৌরসভা"), "paurasabha"),
    Rule("sub post office", ("উপ ডাকঘৰ", "উপ ডাক ঘর"), "up dakghar"),
    Rule("post office", ("ডাকঘৰ", "ডাক ঘর"), "dakghar"),
    Rule("development block", ("উন্নয়ন খণ্ড", "ডন্নয়ন খণ্ড"), "unnayan khanda"),
    Rule("gram panchayat", ("গাঁও পঞ্চায়ত", "গ্রাম পঞ্চায়েত"), "gaon panchayat"),
    Rule("village", ("গাঁও", "গাওঁ", "গ্রাম"), "gaon"),
    Rule("town", ("চহৰ", "শহর"), "chahar"),
    Rule("district", ("জিলা", "জেলা"), "jila"),
    Rule("part", ("অংশ",), "ansh"),
)


def violations(native: str, roman: str) -> List[Rule]:
    """Rules broken by one romanization. Empty when it is a faithful transliteration."""
    return [rule for rule in RULES if rule.violated_by(native, roman)]


def check(entries: Iterable[Tuple[str, str, str]]) -> List[str]:
    """Check a whole table of ``(field, native, roman)``. Empty result means clean."""
    problems: List[str] = []
    for field, native, roman in entries:
        for rule in violations(native, roman):
            problems.append(
                f"{field}: {native!r} -> {roman!r} translates {rule.english!r}; "
                f"expected something like {rule.expected!r}"
            )
    return problems
