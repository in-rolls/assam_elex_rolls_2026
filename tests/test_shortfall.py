"""What may be published, and what may not.

The old rule was exact-or-nothing: one missing row discarded a constituency. Measured on AC8 that
meant throwing away 231,131 rows to avoid 28, and at a 99.4% per-part match rate a 232-part
constituency cleared the bar about a quarter of the time -- which is what a queue stuck at fifty
looks like from outside.

An absolute five-row per-part bar was tried next and repeated the mistake at a higher altitude:
AC24, one part in 177 off by nine rows out of 738, was still discarded whole. Both cases are
pinned below with their real numbers, because the temptation each time was to move the bar until
the thing in front of me passed, and the only defence against that is measured cases in a test.

The rule that survives: whole parts going missing is caught upstream against the published part
count; here a part may be mangled but not badly mangled, and the constituency's total shortfall is
capped as a proportion.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from electors import run


def _found(parts: List[Tuple[int, int]], main_rows: int, measured: int) -> Dict[str, Any]:
    """``parts`` is (printed total, diff) for each mismatching part."""
    residuals = [
        {"part_no": i, "main_rows": total + diff, "roll_total": total, "diff": diff}
        for i, (total, diff) in enumerate(parts, start=1)
    ]
    residuals.sort(key=lambda r: (-abs(r["diff"]), r["part_no"]))
    return {
        "measured": measured,
        "matching": measured - len(residuals),
        "residuals": residuals,
        "main_rows": main_rows,
        "short": sum(abs(d) for _, d in parts),
        "worst": max((abs(d) for _, d in parts), default=0),
        "worst_fraction": max(
            (abs(d) / t for t, d in parts if t),
            default=0.0,
        ),
    }


def test_an_exact_constituency_publishes() -> None:
    assert run.shortfall_verdict(_found([], 231_131, 232)) == ""


def test_ac8_publishes() -> None:
    """Measured: 14 parts short by one to three rows, 28 of 231,131 = 0.012%."""
    parts = [(783, -3), (633, -3)] + [(1052, -2)] * 10 + [(1051, -1), (1021, -1)]
    assert run.shortfall_verdict(_found(parts, 231_131, 232)) == ""


def test_ac24_publishes() -> None:
    """Measured: one part in 177 off by nine rows. 9 of 170,726 = 0.0053%.

    The cleanest constituency measured, discarded by an absolute five-row bar.
    """
    assert run.shortfall_verdict(_found([(738, -9)], 170_726, 177)) == ""


def test_ac19_publishes() -> None:
    """Measured: six parts, worst off by 25 of 985. 45 of 216,865 = 0.021%."""
    parts = [(985, -25), (900, -2), (800, 4), (800, 4), (800, 5), (800, 5)]
    assert run.shortfall_verdict(_found(parts, 216_865, 222)) == ""


def test_a_mangled_part_fails() -> None:
    """A part missing a third of itself was not marginally missed; it was mis-processed."""
    verdict = run.shortfall_verdict(_found([(900, -300)], 216_865, 222))
    assert "missing" in verdict and "of itself" in verdict


def test_many_small_shortfalls_still_fail() -> None:
    """Marginal loss is tolerated, not unlimited: 0.1% of the constituency is the cap."""
    verdict = run.shortfall_verdict(_found([(800, -3)] * 400, 216_865, 222))
    assert "short of the printed totals" in verdict


def test_extra_rows_are_judged_the_same_as_missing_ones() -> None:
    """Inventing a third of a part is exactly as wrong as losing a third."""
    assert "of itself" in run.shortfall_verdict(_found([(900, 300)], 216_865, 222))


def test_the_worst_residual_is_the_one_reported() -> None:
    """It used to report the first two by part number under the word "worst"."""
    verdict = run.shortfall_verdict(_found([(800, -2), (900, -400), (800, -3)], 216_865, 222))
    assert "-400" in verdict
