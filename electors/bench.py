"""The gate every fix has to clear.

Fixes here have been justified by the page that revealed them, and that is precisely how the
sex bias was declared fixed twice before its real cause was found: each attempt improved the
case in front of me, and improving the case in front of you is not evidence.

So a candidate is accepted only when it clears four conditions together:

``in-sample``      the target metric improves on the parts it was diagnosed against
``out-of-sample``  it improves by a comparable margin on parts never inspected
``no degradation`` every other field, and both ground-truth measures, stay within noise
``cost``           seconds per part does not rise materially

The third has been the missing one. **Completeness** -- main-roll rows against the roll's own
closing total -- and the **sex ratio** against the roll's own male/female are the only two
properties here with real ground truth, so they guard every change regardless of which field
it touches. A fix that lifts the EPIC rate while losing electors is not a fix.

The splits below are written down rather than drawn fresh each time, because an out-of-sample
set that gets looked at stops being one. If a page in VALIDATE has to be opened to work out
what is going wrong, that part belongs in DIAGNOSE and something else takes its place.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

#: Pages here may be opened, dumped and stared at.
DIAGNOSE: Sequence[int] = (12, 13, 14, 15, 16)

#: Parts with measured roll totals and recorded baselines. Used to catch collateral damage,
#: never to design against.
REGRESSION: Sequence[int] = tuple(range(1, 12))

#: Everything else is eligible for the held-out sample.
VALIDATE_SEED = 11
VALIDATE_SIZE = 8

#: Where the "before" numbers live. Recorded to a file rather than kept in a docstring,
#: because comparing a fix against a remembered figure is how a regression gets missed.
#:
#: The workflow is two commands. ``bench --record`` measures the current code on all three
#: splits and stores it; make the fix; ``bench`` measures again and prints the gates. That
#: avoids needing both versions of the code alive at once, which is what made the first
#: EPIC comparison a throwaway script instead of something reusable.
BASELINE_PATH = Path("dataset/electors_baseline.json")

#: How far a guarded metric may move before it counts as damage. Sampling noise on a few
#: thousand rows is well under this; a real regression is well over.
TOLERANCE = 0.02

#: Seconds per part may rise by this much before the fix is too expensive to be worth it.
COST_TOLERANCE = 0.10


def validate_parts(available: Sequence[int]) -> List[int]:
    """The held-out set: seeded, and disjoint from the sets that get looked at."""
    eligible = sorted(set(available) - set(DIAGNOSE) - set(REGRESSION))
    if not eligible:
        return []
    return sorted(random.Random(VALIDATE_SEED).sample(eligible, min(VALIDATE_SIZE, len(eligible))))


@dataclass
class GateResult:
    """One condition, and whether the candidate met it."""

    name: str
    passed: bool
    detail: str

    def __str__(self) -> str:
        return f"[{'PASS' if self.passed else 'FAIL'}] {self.name:<16} {self.detail}"


def improvement(before: float, after: float) -> float:
    return after - before


def gate_target(
    label: str,
    before: float,
    after: float,
    minimum: float = 0.02,
) -> GateResult:
    """Did the metric the fix was aimed at actually move, and by enough to be real?"""
    delta = improvement(before, after)
    return GateResult(
        name=label,
        passed=delta >= minimum,
        detail=f"{before:.1%} -> {after:.1%} ({delta:+.1%}, needs +{minimum:.0%})",
    )


def gate_no_damage(
    before: Dict[str, float],
    after: Dict[str, float],
    guarded: Sequence[str],
    tolerance: float = TOLERANCE,
) -> GateResult:
    """Did anything else get worse?

    Checked over *every* guarded metric rather than the one being worked on, because the
    failure this exists to catch is the one nobody was looking for.
    """
    damaged = []
    for key in guarded:
        if key not in before or key not in after:
            continue
        delta = after[key] - before[key]
        if delta < -tolerance:
            damaged.append(f"{key} {before[key]:.1%} -> {after[key]:.1%} ({delta:+.1%})")
    return GateResult(
        name="no degradation",
        passed=not damaged,
        detail="; ".join(damaged) if damaged else f"all {len(guarded)} guarded metrics held",
    )


def gate_cost(
    seconds_before: float,
    seconds_after: float,
    tolerance: float = COST_TOLERANCE,
) -> GateResult:
    """A fix that doubles the runtime has to earn it."""
    if seconds_before <= 0:
        return GateResult("cost", True, "no baseline timing")
    ratio = seconds_after / seconds_before
    return GateResult(
        name="cost",
        passed=ratio <= 1 + tolerance,
        detail=f"{seconds_before:.1f}s -> {seconds_after:.1f}s per part ({ratio - 1:+.0%})",
    )


def run(
    target: str,
    in_sample: Dict[str, float],
    out_of_sample: Dict[str, float],
    regression_before: Dict[str, float],
    regression_after: Dict[str, float],
    guarded: Sequence[str],
    seconds: Optional[Sequence[float]] = None,
    minimum: float = 0.02,
) -> List[GateResult]:
    """All four gates. The candidate is accepted only if every one passes."""
    results = [
        gate_target(
            f"in-sample {target}",
            in_sample.get("before", 0.0),
            in_sample.get("after", 0.0),
            minimum,
        ),
        gate_target(
            f"out-of-sample {target}",
            out_of_sample.get("before", 0.0),
            out_of_sample.get("after", 0.0),
            minimum,
        ),
        gate_no_damage(regression_before, regression_after, guarded),
    ]
    if seconds:
        results.append(gate_cost(seconds[0], seconds[1]))
    return results


def verdict(results: Sequence[GateResult]) -> bool:
    return all(r.passed for r in results)


def render(results: Sequence[GateResult]) -> str:
    lines = [str(r) for r in results]
    lines.append("")
    lines.append("ACCEPTED" if verdict(results) else "REJECTED -- at least one gate failed")
    return "\n".join(lines)


#: Metrics guarded on every change, whatever field the fix is aimed at. The first two are the
#: only ones with real ground truth.
GUARDED: Sequence[str] = (
    "parts_matching_roll_rate",
    "male_share",
    "epic_present",
    "name_present",
    "age_present",
    "sex_present",
    "relation_present",
)


def metrics_from(report: Dict[str, Any], reconciliation: Dict[str, Any]) -> Dict[str, float]:
    """Flatten a quality report into the numbers the gates compare."""
    fill = report.get("fill_rates", {})
    out = {
        "epic_present": fill.get("epic_no", 0.0),
        "name_present": fill.get("name", 0.0),
        "age_present": fill.get("age", 0.0),
        "sex_present": fill.get("sex", 0.0),
        "relation_present": fill.get("relation_name", 0.0),
        "definitely_wrong": report.get("definitely_wrong", {}).get("rate", 0.0),
    }
    out["parts_matching_roll_rate"] = reconciliation.get("parts_matching_roll_rate") or 0.0
    share = reconciliation.get("male_share")
    roll = reconciliation.get("roll_male_share")
    if share is not None and roll:
        # Scored as *closeness to the roll's own split*, not the raw share -- a fix that
        # pushes the ratio past the target is not an improvement either.
        out["male_share"] = 1.0 - abs(share - roll)
    return out


def engine_factory(name: str) -> Callable[[], Any]:
    """The engine under test, resolved late so importing this module stays cheap."""
    from assam_rolls import ocr

    if name == "surya":
        return lambda: ocr.SuryaEngine()
    return lambda: ocr.get_engine("tesseract", lang="asm")


def split_parts(available: Sequence[int]) -> Dict[str, List[int]]:
    """The three sets, by name. Written down once so they cannot drift between runs."""
    return {
        "diagnose": [p for p in DIAGNOSE if p in set(available)],
        "validate": validate_parts(available),
        "regression": [p for p in REGRESSION if p in set(available)],
    }


def load_baseline(path: Path = BASELINE_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_baseline(data: Dict[str, Any], path: Path = BASELINE_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def compare(
    target: str,
    baseline: Dict[str, Any],
    current: Dict[str, Any],
    minimum: float = 0.02,
) -> List[GateResult]:
    """The four gates, from a stored baseline and a fresh measurement.

    ``target`` names the metric the fix was aimed at; everything in ``GUARDED`` is checked
    for damage regardless, on the regression split.
    """
    results: List[GateResult] = []
    for split, label in (("diagnose", "in-sample"), ("validate", "out-of-sample")):
        before = (baseline.get(split) or {}).get(target)
        after = (current.get(split) or {}).get(target)
        if before is None or after is None:
            results.append(GateResult(f"{label} {target}", False, "not measured on both sides"))
        else:
            results.append(gate_target(f"{label} {target}", before, after, minimum))

    results.append(
        gate_no_damage(baseline.get("regression") or {}, current.get("regression") or {}, GUARDED)
    )
    results.append(
        gate_cost(baseline.get("seconds_per_part", 0.0), current.get("seconds_per_part", 0.0))
    )
    return results


class Timer:
    """Wall time per part, so the cost gate measures the real path.

    The first EPIC comparison timed one crop against one crop and looked three times faster.
    The production path also reads the serial strip, which that comparison never ran -- so the
    only cost figure worth quoting is end-to-end.
    """

    def __init__(self) -> None:
        self.started = time.time()
        self.parts = 0

    def done(self, parts: int) -> float:
        self.parts = parts
        return (time.time() - self.started) / max(1, parts)
