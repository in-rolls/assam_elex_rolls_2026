"""What may be published, and what may not.

The old rule was exact-or-nothing: one missing row discarded a constituency. Measured on AC8 that
meant throwing away 231,131 rows to avoid 28, and at a 99.4% per-part match rate a 232-part
constituency cleared the bar about a quarter of the time -- which is what a queue stuck at fifty
looks like from outside.

The new rule tolerates marginal loss and still refuses structural loss. A page of the roll holds
thirty boxes, so these tests hold the line in the gap between three rows and thirty.
"""

from __future__ import annotations

from typing import Any, Dict, List

from electors import run


def _found(diffs: List[int], main_rows: int = 231_131, measured: int = 232) -> Dict[str, Any]:
    residuals = [
        {"part_no": i, "main_rows": 1000 + d, "roll_total": 1000, "diff": d}
        for i, d in enumerate(diffs, start=1)
    ]
    residuals.sort(key=lambda r: (-abs(r["diff"]), r["part_no"]))
    return {
        "measured": measured,
        "matching": measured - len(residuals),
        "residuals": residuals,
        "main_rows": main_rows,
        "short": sum(abs(d) for d in diffs),
        "worst": max((abs(d) for d in diffs), default=0),
    }


def test_an_exact_constituency_publishes() -> None:
    assert run.shortfall_verdict(_found([])) == ""


def test_ac8_publishes() -> None:
    """The measured case: 14 parts short by one to three rows, 0.012% of the constituency."""
    assert run.shortfall_verdict(_found([-3, -3] + [-2] * 10 + [-1, -1])) == ""


def test_a_lost_page_still_fails() -> None:
    """Thirty rows is a page. No aggregate tolerance may ever swallow one."""
    verdict = run.shortfall_verdict(_found([-30]))
    assert "structural" in verdict


def test_a_lost_tile_still_fails() -> None:
    verdict = run.shortfall_verdict(_found([-2, -2, -12]))
    assert "structural" in verdict


def test_many_small_shortfalls_still_fail() -> None:
    """Marginal loss is tolerated, not unlimited: 400 parts short by three is not marginal."""
    verdict = run.shortfall_verdict(_found([-3] * 400))
    assert "short of the printed totals" in verdict


def test_extra_rows_are_judged_the_same_as_missing_ones() -> None:
    """Inventing thirty electors is exactly as wrong as losing thirty."""
    assert "structural" in run.shortfall_verdict(_found([30]))


def test_the_worst_residual_is_the_one_reported() -> None:
    """It used to report the first two by part number under the word "worst"."""
    verdict = run.shortfall_verdict(_found([-2, -40, -3]))
    assert "-40" in verdict or "'diff': -40" in verdict
