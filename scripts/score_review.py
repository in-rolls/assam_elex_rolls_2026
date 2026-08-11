"""Turn the marked review sheet into per-field accuracy, with an interval.

A percentage from 150 boxes is not a number, it is a range. 90% of 150 is 90% ± 5 points at 95%
confidence, and quoting the point estimate alone invites reading a 2-point difference between two
runs as an improvement when it is noise. So every figure here carries its interval, computed the
Wilson way rather than the normal approximation -- the latter misbehaves exactly where these
numbers will land, near 95% and above.

The marks are stored at ``dataset/eval/truth_v2.json`` and are **held out**: they are not to be
used for tuning. The set they replace, ``dataset/eval/truth.json``, was tuned against until it
stopped measuring anything, which is the whole reason this exists.

    python scripts/score_review.py marks.json out/electors/AC010.parquet
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from electors import crops, output  # noqa: E402

FIELDS = ("name", "relation", "age", "sex", "house_no", "epic_no")


def wilson(right: int, total: int, z: float = 1.96) -> tuple[float, float, float]:
    """Point estimate and 95% interval for a proportion.

    Wilson rather than right/total ± z·sqrt(p(1-p)/n): the normal approximation gives intervals
    that run past 100% and are badly wrong for small counts, which is precisely the regime a
    150-box sample of a 95%-accurate field sits in.
    """
    if total == 0:
        return 0.0, 0.0, 0.0
    p = right / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return p, max(0.0, centre - spread), min(1.0, centre + spread)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("marks", help="JSON pasted from the review sheet")
    parser.add_argument("shard")
    args = parser.parse_args()

    marks: Dict[str, Dict[str, bool]] = json.loads(Path(args.marks).read_text(encoding="utf-8"))
    rows = {
        crops.name_for(r["part_no"], r["page_no"], r["box_row"], r["box_col"]): r
        for r in output.read_shard(Path(args.shard))
    }

    print(f"{len(marks)} boxes marked\n")
    print(f"   {'field':<10}{'right':>7}{'of':>5}{'accuracy':>11}   95% interval")
    worst: List[tuple[str, str, Any]] = []
    for field in FIELDS:
        judged = [(k, v[field]) for k, v in marks.items() if field in v]
        right = sum(1 for _, ok in judged if ok)
        p, low, high = wilson(right, len(judged))
        print(f"   {field:<10}{right:>7}{len(judged):>5}{p:>10.1%}   {low:.1%} to {high:.1%}")
        for key, ok in judged:
            if not ok and key in rows:
                row = rows[key]
                value = (
                    f"{row.get('relation_type') or '?'}: {row.get('relation_name')}"
                    if field == "relation"
                    else row.get(field)
                )
                worst.append((field, key, value))

    # An empty field and a wrong one are different failures: one is a box the reader can see was
    # missed, the other is a plausible value that is not what the page says. Only the second is
    # dangerous to a user of the dataset.
    print("\n   of the marked-wrong, how many were blank rather than wrong:")
    for field in FIELDS:
        bad = [w for w in worst if w[0] == field]
        blank = sum(1 for _, _, value in bad if value in (None, "", 0))
        if bad:
            print(f"      {field:<10}{blank:>4} blank, {len(bad) - blank:>4} plausible but wrong")

    print("\n   examples marked wrong:")
    for field, key, value in worst[:12]:
        print(f"      {field:<10}{key:<22}{value!r}")

    Path("dataset/eval").mkdir(parents=True, exist_ok=True)
    Path("dataset/eval/truth_v2.json").write_text(json.dumps(marks, indent=1), encoding="utf-8")
    print("\n   marks saved to dataset/eval/truth_v2.json -- held out, not for tuning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
