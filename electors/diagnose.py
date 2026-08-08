"""What the failures have in common, and what to try about it.

Every root cause found in this stage so far came from dumping pages and staring at them:
*all five boxes with no age line were male*; *six of seven EPIC misses came back empty and one
had a stray slash*; *the failures cluster in the rightmost column*. Each of those was a
**co-occurrence** -- a failure class lining up with an observable feature -- and noticing them
by eye does not scale and does not repeat.

So this looks for them directly. For each failure class it asks which feature of a row
predicts it, and reports the ones where the failure rate is far above base rate. That is the
same signal I was reading off the page dumps, computed instead of noticed.

**It proposes; it does not conclude.** A strong association is a hypothesis about where to
look, and the hypothesis still has to survive `bench` -- in-sample, out-of-sample, no
degradation, cost. The point is to stop the search depending on which page happened to be
opened, not to skip the validation that follows.

Worked example, from the real bug: with `sex` as the failure class and `box_col` as the
feature, the age-line failures sat overwhelmingly in one column. With `sex` against the
*value of another field*, they were all male. Either would have pointed at the line-finding
code in one step.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Sequence, Tuple

#: Row attributes cheap to compute and plausibly causal. Spatial ones matter most: a crop
#: bug shows up as a column or row effect and nothing else.
FEATURES: Sequence[str] = ("box_col", "box_row", "roll_section", "lang", "relation_type")

#: A feature value has to cover at least this many rows before its rate means anything.
MIN_SUPPORT = 30

#: How much worse than base rate a slice must be before it is worth reporting.
MIN_LIFT = 1.5


@dataclass
class Association:
    """One feature value that predicts a failure far more often than the corpus average."""

    failure: str
    feature: str
    value: Any
    rate: float
    base_rate: float
    support: int
    failures: int

    @property
    def lift(self) -> float:
        return self.rate / self.base_rate if self.base_rate else 0.0

    def __str__(self) -> str:
        return (
            f"{self.failure:<18} {self.feature}={str(self.value):<10} "
            f"{self.rate:>6.1%} vs {self.base_rate:.1%} base "
            f"(lift {self.lift:.1f}x, {self.failures}/{self.support})"
        )


#: The failure classes worth explaining. Each maps a row to whether it failed.
FAILURES: Dict[str, Callable[[Dict[str, Any]], bool]] = {
    "no_epic": lambda r: not r.get("epic_no"),
    "no_name": lambda r: not r.get("name"),
    "no_age": lambda r: not r.get("age"),
    "no_sex": lambda r: not r.get("sex"),
    "no_relation": lambda r: not r.get("relation_name"),
    "no_house": lambda r: not r.get("house_no"),
    "name_disagreement": lambda r: "name_disagreement" in (r.get("flags") or ""),
    "unreadable": lambda r: "unreadable" in (r.get("flags") or ""),
}


def associations(
    rows: Sequence[Dict[str, Any]],
    features: Sequence[str] = FEATURES,
    min_support: int = MIN_SUPPORT,
    min_lift: float = MIN_LIFT,
) -> List[Association]:
    """Feature values where a failure class concentrates, strongest first."""
    out: List[Association] = []
    total = len(rows) or 1

    for failure, failed in FAILURES.items():
        flags = [failed(r) for r in rows]
        base = sum(flags) / total
        if not base or base > 0.9:
            # Nothing failing, or everything failing: no slice can stand out.
            continue

        for feature in features:
            buckets: Dict[Any, List[bool]] = defaultdict(list)
            for row, bad in zip(rows, flags):
                buckets[row.get(feature)].append(bad)
            for value, marks in buckets.items():
                if len(marks) < min_support:
                    continue
                rate = sum(marks) / len(marks)
                if base and rate / base >= min_lift:
                    out.append(
                        Association(
                            failure=failure,
                            feature=feature,
                            value=value,
                            rate=rate,
                            base_rate=base,
                            support=len(marks),
                            failures=sum(marks),
                        )
                    )
    return sorted(out, key=lambda a: -a.lift)


#: Signature -> what to try. Deliberately small and specific: a generic suggestion is worth
#: nothing, and each of these names a change that has an obvious implementation.
PROPOSALS: Sequence[Tuple[str, str, str]] = (
    (
        "box_col",
        "no_epic",
        "The EPIC failures concentrate in one column. Its crop is likely clipped or "
        "includes a neighbour: check the column's own left/right/divider against the others.",
    ),
    (
        "box_col",
        "*",
        "This failure is spatial, so suspect the crop rather than the OCR: compare this "
        "column's geometry with the columns that succeed.",
    ),
    (
        "box_row",
        "*",
        "A row effect usually means the row band is off by a few pixels -- an internal rule "
        "or a section header shifting the band. Check row_bands on the affected pages.",
    ),
    (
        "roll_section",
        "*",
        "Supplement pages differ in layout from the main roll (they draw no photo divider). "
        "Check that the geometry derived for the main roll still holds there.",
    ),
    (
        "relation_type",
        "no_sex",
        "Sex failing by relation type points at the age/sex line, not the sex word: the line "
        "is probably not being identified for these rows at all.",
    ),
)


def proposals(found: Sequence[Association]) -> List[str]:
    """Concrete things to try, keyed to the associations actually observed."""
    out: List[str] = []
    seen = set()
    for association in found:
        for feature, failure, text in PROPOSALS:
            if association.feature != feature:
                continue
            if failure not in ("*", association.failure):
                continue
            key = (feature, failure)
            if key in seen:
                continue
            seen.add(key)
            out.append(f"{association.failure} / {association.feature}: {text}")
            break
    return out


def raw_read_shapes(reads: Sequence[str], limit: int = 6) -> List[Tuple[str, int]]:
    """The commonest shapes among failing raw reads.

    Distinguishes *empty* from *garbled*, which want different fixes and looked identical in
    a fill rate. Six of seven EPIC misses returning ``''`` said the segmentation had given up
    on the strip; one returning ``S 106 -, HHK3535/704`` said the regex was too strict.
    """
    shapes = Counter(
        "empty" if not r.strip() else ("short" if len(r.strip()) < 6 else "garbled") for r in reads
    )
    return shapes.most_common(limit)


@dataclass
class Priority:
    """One failure class, sized and characterised so the next move is obvious."""

    failure: str
    affected: int
    share: float
    concentration: float

    @property
    def tractable(self) -> bool:
        """Whether a slice explains enough of it to be worth hunting a cause.

        This is the fix-or-escalate decision and it falls out of the same measurement.
        A failure that concentrates has a mechanical cause -- a crop, a band, a regex -- and
        is cheap to fix for the whole corpus. One spread evenly across every slice is the
        recogniser being wrong in a thousand different ways, which no parser change repairs;
        that is what the expensive second pass is for.
        """
        return self.concentration >= MIN_LIFT

    def __str__(self) -> str:
        route = "fix -- concentrated" if self.tractable else "escalate -- diffuse"
        return (
            f"{self.failure:<18} {self.affected:>6,} rows ({self.share:>5.1%})  "
            f"best slice {self.concentration:.1f}x  ->  {route}"
        )


def priorities(
    rows: Sequence[Dict[str, Any]],
    features: Sequence[str] = FEATURES,
    min_support: int = MIN_SUPPORT,
) -> List[Priority]:
    """Failure classes ranked by how much accuracy is recoverable, biggest first.

    Search order matters more than search cleverness. Working on the class that affects 17%
    of rows beats working on one that affects 0.2%, whatever is more interesting about the
    second -- and the ranking is by *rows*, because that is what the accuracy objective is
    measured in.

    Concentration comes along for free from the association scan and decides the route: fix
    the concentrated ones, escalate the diffuse ones.
    """
    total = len(rows) or 1
    found = associations(rows, features, min_support, min_lift=1.0)
    best: Dict[str, float] = {}
    for association in found:
        best[association.failure] = max(best.get(association.failure, 0.0), association.lift)

    out = []
    for failure, failed in FAILURES.items():
        affected = sum(1 for row in rows if failed(row))
        if not affected:
            continue
        out.append(
            Priority(
                failure=failure,
                affected=affected,
                share=affected / total,
                concentration=best.get(failure, 1.0),
            )
        )
    return sorted(out, key=lambda p: -p.affected)


def render_priorities(ranked: Sequence[Priority]) -> str:
    """What to work on next, and whether to fix it or escalate it."""
    if not ranked:
        return "no failures to rank"
    return "\n".join(
        ["WHAT TO WORK ON NEXT (by rows recoverable)", ""] + [f"   {p}" for p in ranked]
    )


def render(found: Sequence[Association], suggested: Sequence[str]) -> str:
    if not found:
        return "no failure class concentrates anywhere: the errors look spread out"
    lines = ["WHERE FAILURES CONCENTRATE", ""]  # noqa: E501
    lines += [f"   {a}" for a in found[:15]]
    if suggested:
        lines += ["", "WORTH TRYING (hypotheses -- each still has to clear the gates)", ""]
        lines += [f"   - {s}" for s in suggested]
    return "\n".join(lines)
