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

That two-sided test is still not enough once a value can contain *both*. ``করিখাই ফরেষ্ট
ভিলেজ`` has ভিলেজ, the borrowed "village"; a value naming a গাওঁ alongside it would trip the
rule on a word that was never translated. So the loanword side is derived rather than
listed: any native token whose hand-checked romanization already contains the English word
**earns** that word, and the rule stands down. The evidence lives in ``tokens``, where it is
reviewed, instead of being duplicated here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from . import tokens


#: English word (lowercased) -> the native tokens whose reviewed romanization contains it.
#: Built from the lexicon so that adding ``ভিলেজ`` -> *Village* there also teaches the guard
#: that "village" can be honestly earned, with no second list to keep in step.
def _loanword_index() -> Dict[str, Tuple[str, ...]]:
    index: Dict[str, List[str]] = {}
    for native, roman in tokens.LEXICON.items():
        for word in re.findall(r"[a-z]+", roman.lower()):
            index.setdefault(word, []).append(native)
    return {word: tuple(natives) for word, natives in index.items()}


LOANWORDS = _loanword_index()


def loanwords_for(english: str) -> Tuple[Tuple[str, ...], ...]:
    """Per word of ``english``, the native tokens that legitimately produce it.

    Grouped by word, and every group must be satisfied, because a phrase is only borrowed
    if all of it was. ``পুলিচ থানা`` -> "Police Station" still counts as translation: পুলিচ
    earns "police", but nothing in the source earns "station" -- থানা is what was
    translated away.
    """
    return tuple(LOANWORDS.get(word, ()) for word in english.lower().split())


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
        object.__setattr__(self, "_loanwords", loanwords_for(self.english))

    def violated_by(self, native: str, roman: str) -> bool:
        if not roman or not self._pattern.search(roman):  # type: ignore[attr-defined]
            return False
        groups = self._loanwords  # type: ignore[attr-defined]
        if all(any(word in native for word in group) for group in groups):
            return False  # the source borrowed every word of it; echoing it is faithful
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
    # The claim the report makes loudest was the one nothing enforced. ``বিদ্যালয়`` is the
    # single highest-leverage token in the polling-station names -- 7,489 rows -- and the
    # source itself distinguishes it from ``স্কুল``, which it borrowed. Collapsing the two
    # would destroy a distinction the roll took care to record.
    Rule("school", ("বিদ্যালয়", "মহাবিদ্যালয়", "বিদ্যাপীঠ", "নিকেতন"), "vidyalaya"),
    Rule("room", ("কোঠা",), "kotha"),
)


#: Any Bengali-Assamese character. None may survive into a romanization -- if one does,
#: the value is half-converted rather than transliterated. This happened: splitting on
#: whitespace alone left native characters in 112 entries covering 2,426 rows, because
#: ``চক্ৰ(অংশ)`` and ``চিদলা-চৰাং`` are each a single whitespace token.
NATIVE_SCRIPT = re.compile(r"[\u0980-\u09FF]")


def leaked_native(roman: str) -> bool:
    """Whether a romanization still contains native script."""
    return bool(roman) and bool(NATIVE_SCRIPT.search(roman))


def violations(native: str, roman: str) -> List[Rule]:
    """Rules broken by one romanization. Empty when it is a faithful transliteration."""
    return [rule for rule in RULES if rule.violated_by(native, roman)]


def inconsistent(entries: Iterable[Tuple[str, str, str]]) -> Dict[str, List[str]]:
    """Native strings that romanize two different ways. Empty result means clean.

    The table's oldest property: a name means the same thing whichever column it was printed
    in, so ``ডুমডুমা`` cannot be *Doom Dooma* as a post office and *Doomdooma* as a police
    station. It lives here, with the other invariants, because it was previously enforced
    only inside the anchoring run -- which meant hand-editing the shipped CSV, the entire
    reason for shipping a table, could break it and ``check`` would still report success.
    """
    seen: Dict[str, set] = {}
    for _, native, roman in entries:
        if roman:
            seen.setdefault(native, set()).add(roman)
    return {native: sorted(romans) for native, romans in seen.items() if len(romans) > 1}


def check(entries: Iterable[Tuple[str, str, str]]) -> List[str]:
    """Check a whole table of ``(field, native, roman)``. Empty result means clean."""
    entries = list(entries)
    problems: List[str] = [
        f"{native!r} romanizes inconsistently across fields: {romans}"
        for native, romans in sorted(inconsistent(entries).items())
    ]
    for field, native, roman in entries:
        if leaked_native(roman):
            problems.append(
                f"{field}: {native!r} -> {roman!r} still contains native script "
                f"(half-transliterated)"
            )
        for rule in violations(native, roman):
            problems.append(
                f"{field}: {native!r} -> {roman!r} translates {rule.english!r}; "
                f"expected something like {rule.expected!r}"
            )
    return problems
