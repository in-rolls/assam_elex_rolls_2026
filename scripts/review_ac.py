"""Everything worth knowing about one constituency's rows, in one screen.

Reconciliation counts rows. It says nothing about whether the names are right, and a part can
balance perfectly with every name misread. So this reports the counts *and* the things that
would embarrass us if they were wrong, and it separates checks by where their answer comes
from -- because a pipeline grading its own homework is the failure this project keeps finding:

- **against the roll itself**: main-list rows vs each part's printed মূল তালিকা total, the sex
  split vs the published male/female counts, and struck-off entries vs what the info page's net
  implies. None of these numbers were produced here.
- **self-graded**: fill rates, EPIC well-formedness. Useful, but they only say the parser ran.
- **eyeball**: crops of real boxes beside what was extracted from them. The only thing that
  establishes accuracy, and the only one that needs a person.

    python scripts/review_ac.py out/electors/AC001.parquet out/stage1/AC001 --zip <ac.zip>
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from electors import output, run  # noqa: E402

STRICT_EPIC = re.compile(r"^[A-Z]{3}\d{7}$")


def published(ac_no: int) -> dict:
    """What the info pages say, per part: net electors and the sex split."""
    out = {}
    with gzip.open("dataset/parts.jsonl.gz", "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("ac_no") == ac_no:
                out[row["part_no"]] = row
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("shard")
    parser.add_argument("stage1_dir")
    args = parser.parse_args()

    rows = output.read_shard(Path(args.shard))
    ac_no = rows[0]["ac_no"]
    work = Path(args.stage1_dir)
    info = published(ac_no)

    print(f"AC{ac_no}: {len(rows):,} rows from {len({r['part_no'] for r in rows})} parts\n")

    print("AGAINST THE ROLL   (numbers this pipeline did not produce)")
    found = run.reconcile(work, rows)
    print(f"   parts reconciled to their printed total   {found['matching']}/{found['measured']}")
    for item in found["residuals"][:6]:
        print(
            f"      part {item['part_no']}: {item['main_rows']} rows vs "
            f"{item['roll_total']} printed ({item['diff']:+d})"
        )

    struck = run.struck_off_check(
        work, rows, {p: r["electors_total"] for p, r in info.items() if r.get("electors_total")}
    )
    print(
        f"   struck off, detected vs implied           "
        f"{struck['detected']:,} vs {struck['implied']:,}"
    )

    male = sum(1 for r in rows if r.get("sex") == "M")
    female = sum(1 for r in rows if r.get("sex") == "F")
    pub_m = sum(r.get("electors_male") or 0 for r in info.values())
    pub_f = sum(r.get("electors_female") or 0 for r in info.values())
    if male + female and pub_m + pub_f:
        print(
            f"   male share, extracted vs published        "
            f"{male / (male + female):.1%} vs {pub_m / (pub_m + pub_f):.1%}"
        )

    print("\nSELF-GRADED   (says the parser ran, not that it was right)")
    for field in ("name", "relation_name", "house_no", "epic_no", "sex"):
        filled = sum(1 for r in rows if r.get(field))
        print(f"   {field:<14} filled {filled / len(rows):6.1%}")
    ages = [r["age"] for r in rows if r.get("age")]
    print(
        f"   {'age':<14} filled {len(ages) / len(rows):6.1%}"
        f"   median {sorted(ages)[len(ages) // 2] if ages else '-'}"
    )
    valid = [r["epic_no"] for r in rows if STRICT_EPIC.fullmatch(r.get("epic_no") or "")]
    print(f"   {'epic':<14} well formed {len(valid) / len(rows):6.1%}")
    # A repeated EPIC is one voter appearing twice, which is either a real duplicate on the
    # roll or two boxes misread into the same string. Both are worth knowing; neither is
    # visible from a fill rate.
    repeats = collections.Counter(valid)
    dupes = {e: n for e, n in repeats.items() if n > 1}
    print(
        f"   {'epic':<14} distinct {len(repeats):,} of {len(valid):,}"
        + (f"   {sum(dupes.values()) - len(dupes):,} rows share an EPIC" if dupes else "")
    )

    codes = collections.Counter(r.get("status_code") for r in rows if r.get("status_code"))
    print(f"   {'status codes':<14} {dict(codes)}")
    flags = collections.Counter(f for r in rows for f in (r.get("flags") or "").split(",") if f)
    print(f"   {'flags':<14} {dict(flags.most_common(6))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
