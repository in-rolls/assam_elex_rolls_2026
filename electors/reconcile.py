"""Whether a constituency is fit to publish, and on what evidence.

Every check that decided something during this stage's development is here, because the ones
that lived in shell history ran once, against one constituency, and could not be re-run by
anybody -- including the person who wrote them.

The checks are grouped by **where their answer comes from**, and that grouping is the point:

- **against the roll** -- numbers the publisher printed and this pipeline did not produce. The
  part count on the info pages, the closing page's মূল তালিকা total, the published male/female
  split. These are the only checks that can contradict us.
- **between the stages** -- boxes found against rows written, tiles against source pixels. These
  catch losing our own work, which happened twice.
- **self-graded** -- fill rates, EPIC shape, duplicate identifiers. They say the parser ran.

**What none of them can see**: whether a name is right. A constituency can pass every check here
with every name misread, so a PASS verdict is a statement about completeness, not accuracy. That
belongs to the hand-read sample and nothing else.

The verdict is deliberately narrow. PASS requires every part published to be extracted, every
part to carry a printed total, and every one of those totals to match. Anything less is FAIL with
the reason named, because AC101 shipped 113 of 210 parts while reporting success and the number
that would have caught it was sitting unused in the repository.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

#: Three letters and seven digits, as printed. Looser patterns match damaged reads and make the
#: duplicate count meaningless.
EPIC = re.compile(r"^[A-Z]{3}\d{7}$")


@dataclass
class Check:
    """One question, its answer, and whether the answer is acceptable."""

    name: str
    passed: bool
    detail: str
    #: False where the check cannot contradict us -- fill rates and the like.
    external: bool = True


@dataclass
class Verdict:
    """What a constituency is, on the evidence."""

    ac_no: int
    checks: List[Check] = field(default_factory=list)

    @property
    def failed(self) -> List[Check]:
        return [c for c in self.checks if not c.passed]

    @property
    def ok(self) -> bool:
        """PASS only on the checks that can contradict us, and only on completeness.

        A self-graded check never decides a verdict: a fill rate of 99% is equally consistent
        with 99% correct and 40% correct, and letting it vote would make the verdict mean less
        than it appears to.

        **A verdict with no external checks fails.** An audit found ``ok`` true for an empty
        verdict, which is the whole failure mode of this project in one line: nothing was
        checked, so nothing was wrong. Passing requires evidence, not the absence of complaint.
        """
        external = [c for c in self.checks if c.external]
        return bool(external) and all(c.passed for c in external)

    def add(self, name: str, passed: bool, detail: str, external: bool = True) -> None:
        self.checks.append(Check(name, passed, detail, external))


def parts_complete(rows: Sequence[Dict[str, Any]], published: int) -> Check:
    """Every part the info pages publish is present.

    The check AC101 needed. It shipped 113 of its 210 parts, reconciled every one of them, and
    reported success -- because reconciliation only scores parts that reached the shard, so a
    part that vanished entirely was invisible to it. A check that cannot see absence is not a
    completeness check.
    """
    found = len({r["part_no"] for r in rows})
    return Check(
        "parts complete",
        found == published,
        f"{found} of {published} published parts",
    )


def rows_against_printed(
    rows: Sequence[Dict[str, Any]], totals: Dict[int, int], main: str = "main"
) -> Check:
    """Main-list rows against each part's own printed closing total.

    Main list only: supplements are counted separately on the closing page, so scoring every row
    against that total makes a part with a supplement look over-extracted.
    """
    counted: Dict[int, int] = collections.Counter(
        r["part_no"] for r in rows if (r.get("roll_section") or main) == main
    )
    # Compared as **sets of parts**, not as counts. An audit reproduced rows for parts {1, 2}
    # against totals for parts {1, 3} passing every check: part 2 was never measured and phantom
    # part 3 "matched" zero rows. Counting the two collections agrees; asking whether they
    # describe the same parts does not.
    have = {r["part_no"] for r in rows}
    phantom = sorted(set(totals) - have)
    off = [(p, counted.get(p, 0), t) for p, t in sorted(totals.items()) if counted.get(p, 0) != t]
    detail = f"{len(totals) - len(off)} of {len(totals)} measured parts match"
    if phantom:
        detail += f"; {len(phantom)} totals name parts with no rows: {phantom[:5]}"
    if off:
        detail += f"; worst {sorted(off, key=lambda x: abs(x[1] - x[2]))[-1]}"
    return Check("rows match printed totals", not off and not phantom and bool(totals), detail)


def measured_coverage(rows: Sequence[Dict[str, Any]], totals: Dict[int, int]) -> Check:
    """How many parts carry a printed total at all -- how much of the check above applies.

    Reported apart from the result of that check, because 205 of 205 matching means something
    very different when 205 is every part than when it is four fifths of them.
    """
    have = {r["part_no"] for r in rows}
    # Which parts, not how many. Equal counts over different part numbers is the shape the audit
    # reproduced, and it passed.
    unmeasured = sorted(have - set(totals))
    return Check(
        "every part measurable",
        bool(have) and not unmeasured,
        f"{len(have & set(totals))} of {len(have)} parts have a printed total"
        + (f"; unmeasured {unmeasured[:5]}" if unmeasured else ""),
    )


def sex_against_published(
    rows: Sequence[Dict[str, Any]], male: int, female: int, tolerance: float = 0.02
) -> Check:
    """The extracted male share against the published one.

    Bounds **bias, not error**: symmetric misreads cancel and leave the ratio untouched, so this
    can pass while a tenth of rows are wrong. It earns its place because the failure it does
    catch is the one that destroys aggregate statistics -- an earlier version ran 44.2% male
    against a published 50.8%, and only the ratio noticed.
    """
    got_m = sum(1 for r in rows if r.get("sex") == "M")
    got_f = sum(1 for r in rows if r.get("sex") == "F")
    if not (got_m + got_f) or not (male + female):
        return Check("sex ratio", False, "no sex values to compare", external=True)
    ours, theirs = got_m / (got_m + got_f), male / (male + female)
    return Check(
        "sex ratio",
        abs(ours - theirs) <= tolerance,
        f"{ours:.1%} extracted against {theirs:.1%} published",
    )


def rows_match_boxes(rows: Sequence[Dict[str, Any]], boxes_per_part: Dict[int, int]) -> Check:
    """Every box stage one found became a row.

    Between the stages rather than against the roll, but it caught nothing only because nothing
    was wrong: when AC101 lost 97 parts it was this comparison, run by hand, that showed the
    shard held 113 parts' worth of rows for 210 parts' worth of boxes.
    """
    counted = collections.Counter(r["part_no"] for r in rows)
    lost = {p: (n, counted.get(p, 0)) for p, n in boxes_per_part.items() if counted.get(p, 0) != n}
    return Check(
        "rows match boxes found",
        not lost,
        (
            f"{len(lost)} parts differ"
            if lost
            else f"{sum(boxes_per_part.values()):,} boxes, all kept"
        ),
    )


def duplicate_epics(rows: Sequence[Dict[str, Any]]) -> Check:
    """Identifiers appearing on more than one row.

    Self-graded, and reported rather than enforced: a duplicate is either one voter listed twice
    on the roll or two boxes misread into the same string, and this cannot tell them apart. What
    made it worth keeping is that rows sharing an EPIC turned out to be eleven times more likely
    to have an unreadable name, which is what implicated the digit-position repair.
    """
    valid = [r["epic_no"] for r in rows if EPIC.fullmatch(r.get("epic_no") or "")]
    repeats = collections.Counter(valid)
    shared = sum(n for n in repeats.values() if n > 1)
    return Check(
        "epic duplicates",
        True,
        f"{shared:,} rows share an EPIC, of {len(valid):,} well formed",
        external=False,
    )


def fill_rates(rows: Sequence[Dict[str, Any]], fields: Sequence[str]) -> Check:
    """How often each field is non-empty. Says the parser ran, not that it was right."""
    parts = [
        f"{f} {sum(1 for r in rows if r.get(f) not in (None, '')) / len(rows):.1%}" for f in fields
    ]
    return Check("fill rates", True, ", ".join(parts), external=False)


def judge(
    ac_no: int,
    rows: Sequence[Dict[str, Any]],
    published_parts: int,
    totals: Dict[int, int],
    male: int = 0,
    female: int = 0,
    boxes_per_part: Optional[Dict[int, int]] = None,
) -> Verdict:
    """Every check, in one verdict."""
    verdict = Verdict(ac_no=ac_no)
    if not rows:
        verdict.add("rows present", False, "the shard is empty")
        return verdict

    verdict.checks.append(parts_complete(rows, published_parts))
    verdict.checks.append(measured_coverage(rows, totals))
    verdict.checks.append(rows_against_printed(rows, totals))
    if male or female:
        verdict.checks.append(sex_against_published(rows, male, female))
    if boxes_per_part:
        verdict.checks.append(rows_match_boxes(rows, boxes_per_part))
    verdict.checks.append(duplicate_epics(rows))
    verdict.checks.append(fill_rates(rows, ("name", "relation_name", "age", "sex", "epic_no")))
    return verdict


def render(verdicts: Sequence[Verdict]) -> str:
    """The verdicts, with the failures named rather than left as numbers to interpret."""
    lines = [f"{'AC':>5}  {'verdict':<8} evidence"]
    for verdict in verdicts:
        mark = "PASS" if verdict.ok else "FAIL"
        lines.append(f"{verdict.ac_no:>5}  {mark:<8}")
        for check in verdict.checks:
            flag = " " if check.passed else "!"
            where = "" if check.external else "   (self-graded)"
            lines.append(f"       {flag} {check.name:<26}{check.detail}{where}")
    passed = sum(1 for v in verdicts if v.ok)
    lines.append("")
    lines.append(f"   {passed} of {len(verdicts)} constituencies pass")
    lines.append("   a pass is completeness, not accuracy: no check here can see a wrong name")
    return "\n".join(lines)
