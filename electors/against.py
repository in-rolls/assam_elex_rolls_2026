"""Gate a parsing change against a git revision, over cached OCR text.

The recorded-baseline flow -- measure, make the fix, measure again -- needs the "before" run to
have happened before the fix existed. That is easy to forget, and a baseline file that has gone
stale compares the fix against a number nobody can place any more.

Git already stores every previous version of the parser, so this loads the field module out of
a revision and runs both over the same cached lines. There is nothing to record beforehand and
nothing to keep in sync, the comparison can be re-run months later, and a stale baseline cannot
exist because the baseline is a commit.

Both sides parse identical text, so no part of the difference can be OCR drift. This is exact
for anything downstream of the text and blind to crop geometry, scale and engine -- those
change the text itself, and both sides would replay the same old text and report no difference.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from typing import Any, Callable, Dict, List, Sequence

from . import bench, quality, replay, validate


def field_module(ref: str):
    """``electors/fields.py`` as it stood at ``ref``, executed inside the package.

    Loaded rather than reimplemented. An earlier comparison rewrote the old label matcher from
    memory, and a rewrite that drifts makes the fix look better or worse than it was.
    """
    source = subprocess.run(
        ["git", "show", f"{ref}:electors/fields.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    name = f"electors._at_{ref.replace('~', '_').replace('^', '_').replace('/', '_')}"
    spec = importlib.util.spec_from_loader(name, loader=None)
    if spec is None:  # pragma: no cover -- spec_from_loader does not return None here
        raise RuntimeError(f"could not build a module spec for {ref}")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "electors"
    sys.modules[name] = module
    exec(compile(source, f"<{ref}:electors/fields.py>", "exec"), module.__dict__)
    return module


def rows_through(assembler: Callable[..., Any], parts: Sequence[int], ac: int = 1):
    """Replay every captured box through one assembler."""
    from . import fields

    saved = fields.assemble
    fields.assemble = assembler
    try:
        return replay.replay_parts(parts, ac=ac)
    finally:
        fields.assemble = saved


def metrics_for(rows: Sequence[Dict[str, Any]], roll_totals: Dict[Any, Any]) -> Dict[str, float]:
    checks = validate.reconcile(rows, validate.load_part_totals(), roll_totals)
    summary = validate.summarize(checks, rows)
    return bench.metrics_from(quality.report(rows, summary), summary)


def split(parts: Sequence[int]) -> Dict[str, List[int]]:
    """In-sample against out-of-sample, by the written-down diagnosis set."""
    diagnosed = set(bench.DIAGNOSE)
    return {
        "in-sample": [p for p in parts if p in diagnosed],
        "out-of-sample": [p for p in parts if p not in diagnosed],
    }


def compare(
    ref: str,
    parts: Sequence[int],
    roll_totals: Dict[Any, Any],
    target: str = "definitely_wrong",
    higher_is_better: bool = False,
    minimum: float = 0.005,
    ac: int = 1,
) -> Dict[str, Any]:
    """Both sides, per split and combined, with the gates the combined numbers imply."""
    from . import fields

    baseline = field_module(ref).assemble
    out: Dict[str, Any] = {"ref": ref, "parts": list(parts), "splits": {}}

    for label, subset in split(parts).items():
        if not subset:
            # Named rather than dropped: a split with nothing in it has not been measured, and
            # a gate that quietly skips it passes by omission.
            out["splits"][label] = None
            continue
        before = metrics_for(rows_through(baseline, subset, ac), roll_totals)
        after = metrics_for(rows_through(fields.assemble, subset, ac), roll_totals)
        out["splits"][label] = {"parts": subset, "before": before, "after": after}

    old = rows_through(baseline, parts, ac)
    new = rows_through(fields.assemble, parts, ac)
    if len(old) != len(new):
        raise RuntimeError("the two sides replayed different numbers of boxes")

    out["rows"] = len(new)
    out["before"] = metrics_for(old, roll_totals)
    out["after"] = metrics_for(new, roll_totals)
    out["gates"] = [
        bench.gate_target(
            f"in-sample {target}",
            *_pair(out["splits"].get("in-sample"), target),
            minimum,
            higher_is_better,
        ),
        bench.gate_target(
            f"out-of-sample {target}",
            *_pair(out["splits"].get("out-of-sample"), target),
            minimum,
            higher_is_better,
        ),
        bench.gate_no_damage(out["before"], out["after"], bench.GUARDED),
    ]
    return out


def _pair(measured: Any, target: str):
    """Before and after for one split, or a pair that cannot pass when it was not measured."""
    if not measured:
        return (0.0, 0.0)
    return (measured["before"].get(target, 0.0), measured["after"].get(target, 0.0))


def render(found: Dict[str, Any], target: str = "definitely_wrong") -> str:
    lines = [f"against {found['ref']}, replaying {found['rows']:,} cached rows", ""]
    for label, measured in found["splits"].items():
        if not measured:
            lines.append(f"   {label:<14} NOT MEASURED -- no captured parts in this split")
            continue
        before = measured["before"].get(target, 0.0)
        after = measured["after"].get(target, 0.0)
        lines.append(
            f"   {label:<14} {target} {before:.1%} -> {after:.1%} ({after - before:+.1%})"
            f"   parts {measured['parts']}"
        )

    lines += ["", f"   {'metric':<26} {'before':>9} {'after':>9} {'delta':>10}"]
    for key in sorted(set(found["before"]) | set(found["after"])):
        before, after = found["before"].get(key), found["after"].get(key)
        if before is None or after is None:
            lines.append(f"   {key:<26} {'--':>9} {'--':>9}   NOT MEASURED")
            continue
        mark = "   <-- DAMAGE" if key in bench.GUARDED and after - before < -bench.TOLERANCE else ""
        lines.append(f"   {key:<26} {before:>8.1%} {after:>8.1%} {after - before:>+9.1%}{mark}")

    lines += [""] + [f"   {gate}" for gate in found["gates"]]
    lines += ["", "   ACCEPTED" if bench.verdict(found["gates"]) else "   REJECTED"]
    return "\n".join(lines)
