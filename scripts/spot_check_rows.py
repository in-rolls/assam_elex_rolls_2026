"""Put extracted rows next to the pixels they came from, so a person can judge them.

Reconciliation counts rows against each part's printed total. That is the only check here the
pipeline did not itself produce, and it is worth what it costs -- but it says nothing about
whether the *names* are right. A part can reconcile perfectly with every name misread.

So this cuts each sampled row's own box out of its source page and writes it beside what the
pipeline says that box contains. Reading twenty of those takes a couple of minutes and is the
only thing that establishes accuracy; every automatic measure here is either self-graded or a
count.

Sampled deterministically, and biased towards what is newest and least proven -- rows on pages
the partial-page recovery brought back, which have never been checked at the text level.

    python scripts/spot_check_rows.py <shard.parquet> <stage1 dir> --zip <AC zip> -n 20
"""

from __future__ import annotations

import argparse
import random
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assam_rolls import render  # noqa: E402
from electors import crops, output, vision_part  # noqa: E402

FIELDS = ("serial_no", "name", "relation_name", "house_no", "age", "sex", "epic", "roll_section")


def partial_pages(part_dir: Path) -> set:
    """Pages whose boxes do not fill the sheet -- what the recovery brought back.

    A full page holds thirty boxes. Anything less is a page that ran out of electors, which is
    exactly the shape that used to be discarded, so those rows deserve the closest look.
    """
    manifest = crops.read_manifest(part_dir)
    per_page: Dict[int, int] = {}
    for row in manifest.values():
        per_page[row["page_no"]] = per_page.get(row["page_no"], 0) + 1
    return {page for page, count in per_page.items() if count < 30}


def sample(rows: List[Dict[str, Any]], interesting: set, count: int) -> List[Dict[str, Any]]:
    """Half from the partial pages, half from everywhere -- seeded, so a finding is reproducible."""
    rng = random.Random(20260810)
    odd = [r for r in rows if (r["part_no"], r["page_no"]) in interesting]
    rest = [r for r in rows if (r["part_no"], r["page_no"]) not in interesting]
    rng.shuffle(odd)
    rng.shuffle(rest)
    half = count // 2
    picked = odd[:half] + rest[: count - min(half, len(odd))]
    return picked[:count]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("shard")
    parser.add_argument("stage1_dir")
    parser.add_argument("--zip", required=True)
    parser.add_argument("-n", type=int, default=20)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    stage_dir = Path(args.stage1_dir)
    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="spotcheck-"))
    out.mkdir(parents=True, exist_ok=True)

    rows = output.read_shard(Path(args.shard))
    interesting = set()
    for part_dir in sorted(stage_dir.glob("part*")):
        number = int(part_dir.name.replace("part", ""))
        interesting |= {(number, page) for page in partial_pages(part_dir)}

    picked = sample(rows, interesting, args.n)
    by_part: Dict[int, List[Dict[str, Any]]] = {}
    for row in picked:
        by_part.setdefault(row["part_no"], []).append(row)

    zip_path = Path(args.zip)
    refs = {r.part_no: r.pdf_name for r in render.iter_zip_parts(zip_path)}
    manifests = {
        int(p.name.replace("part", "")): crops.read_manifest(p) for p in stage_dir.glob("part*")
    }

    for part_no, wanted in sorted(by_part.items()):
        payload = render.read_pdf_bytes(zip_path, refs[part_no])
        with tempfile.TemporaryDirectory() as tmp:
            images = vision_part.rasterize(400)(payload, Path(tmp))
            for row in wanted:
                key = crops.name_for(part_no, row["page_no"], row["box_row"], row["box_col"])
                found = manifests[part_no].get(key)
                if not found:
                    continue
                page = images[row["page_no"] - 1]
                crop = page.crop((found["left"], found["top"], found["right"], found["bottom"]))
                mark = "PARTIAL" if (part_no, row["page_no"]) in interesting else "full"
                name = f"p{part_no}_pg{row['page_no']}_r{row['box_row']}c{row['box_col']}.png"
                crop.save(out / name)
                print(f"\n{name}   [{mark} page]")
                for field in FIELDS:
                    print(f"    {field:<14} {row.get(field)!r}")
            for image in images:
                image.close()

    print(f"\n{len(picked)} crops written to {out}")
    print("open them beside the values above; the pixels are the ground truth")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
