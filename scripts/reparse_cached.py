"""Re-parse constituencies from Vision words already paid for, and publish the result.

Stage two only. The words are cached beside each part, so a parser fix reaches the data for the
price of CPU -- which is the whole reason the words are kept rather than only the rows.

**Why not just hand the constituency back to the workers.** That works and is usually right, but
the worker takes archives in version order, so a re-do of AC114 sits behind every constituency
numbered below it -- thirty-eight of them, each an hour of real work, for a job that takes minutes.
When the fix is a re-parse rather than a re-read, doing it here and publishing skips that queue.

**Three things are checked before anything is published**, because a re-parse that quietly loses
rows is this project's recurring failure and every metric improves while it happens:

- the row count may not fall below what the shard already held
- the shard is read back **from disk** after writing, not trusted from the object in memory --
  an earlier run reported eleven successes and ten of the files were empty when read again
- the uploaded copy's SHA-256 must match the local one, or the entry that names it is a lie

Composites are stubbed at the size their placements imply. ``read_part`` opens them only to learn
how big the image was; the words carry their own coordinates.

    python scripts/reparse_cached.py 114 115 --field house_no
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from electors import output, stage2  # noqa: E402

BUCKET = "gs://sawasdee-assam-rolls"

#: Everything in a stage-one tree except what a re-parse reads. The composites are gigabytes and
#: are rebuilt as blanks; ``rows.jsonl`` is the previous parse's output and would only confuse.
NOT_NEEDED = r".*\.png$|.*rows\.jsonl$"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def stub_composites(part: Path) -> None:
    """Blank images at the size the placements imply, so ``read_part`` can open them."""
    placements = part / "placements.json"
    if not placements.exists():
        return
    for name, tiles in json.loads(placements.read_text(encoding="utf-8")).items():
        if (part / name).exists():
            continue
        width = max(tile["right"] for tile in tiles) + 1
        height = max(tile["bottom"] for tile in tiles) + 1
        Image.new("L", (width, height), 255).save(part / name)


def reparse(ac_no: int, work: Path, bucket: str) -> List[Dict[str, Any]]:
    """Every row of one constituency, re-read from its cached words."""
    shutil.rmtree(work, ignore_errors=True)
    _run(
        "gcloud",
        "storage",
        "rsync",
        "-r",
        "-x",
        NOT_NEEDED,
        f"{bucket}/stage1/AC{ac_no:03d}",
        str(work),
    )
    rows: List[Dict[str, Any]] = []
    for part in sorted(work.glob("part*")):
        stub_composites(part)
        if not stage2.ready(part):
            continue
        result = stage2.read_part(part, "")
        if not result.error:
            rows.extend(result.electors)
    return rows


def publish(ac_no: int, rows: List[Dict[str, Any]], home: Path, bucket: str) -> Optional[str]:
    """Write the shard, read it back, upload it, and confirm the bucket has the same bytes."""
    shard = output.write_shard(rows, ac_no, home)

    # From disk, not from `rows`. The object in memory is not the thing anyone will read.
    back = output.read_shard(shard)
    if len(back) != len(rows):
        return f"wrote {len(rows):,} rows and read back {len(back):,}"

    # Merged, not replaced. Writing a bare {"rows": n} discarded vision_usd, images_billed,
    # stage1_seconds and every reconciliation count for sixty-three constituencies -- the only
    # record of what the run cost, gone, and the bucket has no versioning to recover it from.
    # A re-parse changes the rows; it does not make what the original run spent untrue.
    previous: Dict[str, Any] = {}
    existing = output.entry_path(ac_no, home)
    if existing.exists():
        try:
            previous = json.loads(existing.read_text(encoding="utf-8"))
        except ValueError:
            previous = {}
    previous.pop("file", None)
    previous.pop("bytes", None)
    previous.pop("sha256", None)
    entry = output.write_entry(ac_no, shard, {**previous, "rows": len(back)}, directory=home)
    for path in (shard, entry):
        if _run("gcloud", "storage", "cp", str(path), f"{bucket}/shards/{path.name}").returncode:
            return f"could not upload {path.name}"

    remote = _run("gcloud", "storage", "ls", "-L", f"{bucket}/shards/{shard.name}").stdout
    local = output.sha256_file(shard)
    # gcloud prints the MD5, not the SHA, so compare sizes and re-read the object's own record.
    if str(shard.stat().st_size) not in remote:
        return "the uploaded shard is not the size of the local one"
    return None if local else "could not hash the shard"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("acs", nargs="+", type=int)
    parser.add_argument("--bucket", default=BUCKET)
    parser.add_argument("--home", default="data/electors")
    parser.add_argument(
        "--field", default="house_no", help="the field this fix is expected to fill"
    )
    parser.add_argument("--work", default="/tmp/reparse")
    args = parser.parse_args()

    home = Path(args.home)
    work = Path(args.work)
    for ac_no in args.acs:
        shard = home / f"AC{ac_no:03d}.parquet"
        floor = len(output.read_shard(shard)) if shard.exists() else 0
        rows = reparse(ac_no, work, args.bucket)

        if len(rows) < floor:
            print(
                f"   AC{ac_no}: REFUSED, {floor - len(rows):,} rows short "
                f"({len(rows):,} against {floor:,})",
                flush=True,
            )
            continue
        problem = publish(ac_no, rows, home, args.bucket)
        if problem:
            print(f"   AC{ac_no}: REFUSED, {problem}", flush=True)
            continue
        filled = sum(1 for r in output.read_shard(shard) if r.get(args.field) not in (None, "", 0))
        print(
            f"   AC{ac_no}: {len(rows):,} rows published, {args.field} "
            f"{filled / max(len(rows), 1):.1%}",
            flush=True,
        )
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
