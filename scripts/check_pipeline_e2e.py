"""Run the whole chain on prepared parts without buying a single image.

``check_stage2_join.py`` proves words land in the right box. This proves everything after that:
rows cached per part so a resumed run does not re-pay, main-list rows reconciled against each
part's own printed closing total, a Parquet shard written, and a manifest that accumulates a row
per constituency rather than replacing the last one.

Vision is replaced by the same synthetic reply the join check uses, so the rows are real in
every respect that this script tests -- count, page, box, section, part -- and fictional only in
the text, which nothing here looks at. What it cannot test is the HTTP call, which is covered by
its own tests, and the reading quality, which is not a pipeline property.

Run it against a directory stage one has already prepared:

    python scripts/check_pipeline_e2e.py out/stage1/AC001
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check_stage2_join import reply_for  # noqa: E402

from electors import output, run, stage2  # noqa: E402


def seeded_read_part(part_dir: Path, api_key: str, annotate=None):
    """stage2.read_part with the API replaced by a reply minted from the placements."""
    placements = stage2.placements_of(part_dir)
    order = list(placements)
    seen = {"n": 0}

    def fake(_images):
        reply = reply_for(placements[order[seen["n"]]])
        seen["n"] += 1
        return reply

    return _real(part_dir, api_key, annotate=fake)


_real = stage2.read_part


def main(stage_dir: Path, scratch: Path) -> int:
    if scratch.exists():
        shutil.rmtree(scratch)
    work = scratch / "work" / stage_dir.name
    shutil.copytree(stage_dir, work)
    zip_path = Path("data/ac_rolls/AC1_ASM.zip")

    stage2.read_part = seeded_read_part  # type: ignore[assignment]
    try:
        first = run.run_ac(
            zip_path,
            work,
            api_key="",
            shard_dir=scratch / "shards",
            manifest=scratch / "manifest.json",
            limit=5,
            log=lambda message: None,
        )
        print(run.render_report([first]))

        print("\n  --- resumed, nothing should be re-read ---")
        billed_before = sum(1 for p in work.glob("part*") if (p / run.ROWS).exists())
        again = run.run_ac(
            zip_path,
            work,
            api_key="",
            shard_dir=scratch / "shards",
            manifest=scratch / "manifest.json",
            limit=5,
            log=lambda message: None,
        )
        print(f"  parts cached before resume   {billed_before}")
        print(f"  rows first run               {first.rows:,}")
        print(f"  rows after resume            {again.rows:,}")
    finally:
        stage2.read_part = _real  # type: ignore[assignment]

    shard = scratch / "shards" / output.shard_path(1, Path(".")).name
    rows = output.read_shard(shard)
    manifest = json.loads((scratch / "manifest.json").read_text(encoding="utf-8"))

    traceable = sum(1 for r in rows if r.get("pdf_sha256") and r.get("source_pdf"))
    sections = {r.get("roll_section") for r in rows}
    print()
    print("  PIPELINE END TO END")
    print(f"     shard                     {shard.name}  ({shard.stat().st_size / 1e6:.1f} MB)")
    print(f"     rows in shard             {len(rows):,}")
    print(f"     rows traceable to bytes   {traceable:,}  ({traceable / len(rows):.1%})")
    print(f"     roll sections present     {sorted(s for s in sections if s)}")
    print(f"     manifest constituencies   {sorted(manifest)}")
    print(
        f"     manifest AC1              {manifest['1']['rows']:,} rows, "
        f"{manifest['1']['parts_matching_roll']}/{manifest['1']['parts_measured']} reconciled"
    )

    ok = (
        len(rows) == first.rows == again.rows
        and traceable == len(rows)
        and first.parts_matching == first.parts_measured == 5
    )
    print()
    print("  every property held" if ok else "  SOMETHING DID NOT HOLD")
    return 0 if ok else 1


if __name__ == "__main__":
    scratch = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("out/e2e_check")
    raise SystemExit(main(Path(sys.argv[1]), scratch))
