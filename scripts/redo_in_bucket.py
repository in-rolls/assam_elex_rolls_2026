"""Hand a finished constituency back to the workers, so it is re-read with the current code.

A worker skips any constituency whose shard is already in the bucket, and that shard is the only
record that it is done. So removing the shard is how you ask for it again -- and because the worker
pulls the stage-one tree back before it starts, the parts it already prepared and the Vision words
it already paid for are reused. What gets re-done is the parse, which is CPU.

**Why the bucket and not the laptop.** The same re-parse was applied locally twice and undone both
times: ``deliver.already_here`` compares a shard's SHA-256 against the bucket's and treats any
difference as a corrupt download, so a corrected shard was replaced by the stale one within the
five-minute poll. The bucket is authoritative by design. Correcting it there and pulling afterwards
works with that rather than against it.

**Nothing is deleted.** The shard, its entry and its verification bundle are moved under
``archive/`` first. A constituency that comes back worse has to be recoverable, and the old row
count is the only thing that can prove the new one did not lose anything.

    python scripts/redo_in_bucket.py 114 115 116          # dry run
    python scripts/redo_in_bucket.py 114 115 116 --yes
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from electors import output  # noqa: E402

BUCKET = "gs://sawasdee-assam-rolls"

#: Where a superseded shard goes. Dated, because a constituency may be re-done more than once and
#: the point of keeping the old one is being able to say which run produced which numbers.
ARCHIVE = "archive/superseded"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def rows_now(ac_no: int, home: Path) -> int:
    """What the shard holds before it is handed back -- the floor the re-run must clear."""
    shard = home / f"AC{ac_no:03d}.parquet"
    return len(output.read_shard(shard)) if shard.exists() else 0


def hand_back(ac_no: int, bucket: str, stamp: str, dry_run: bool = True) -> List[str]:
    """Move a constituency's shard aside and clear its claim, so a worker takes it again."""
    moved: List[str] = []
    for suffix in ("parquet", "entry.json", "verify.json"):
        source = f"{bucket}/shards/AC{ac_no:03d}.{suffix}"
        if _run("gcloud", "storage", "ls", source).returncode != 0:
            continue
        target = f"{bucket}/{ARCHIVE}/{stamp}/AC{ac_no:03d}.{suffix}"
        if not dry_run:
            # Copy then remove rather than mv: mv on a missing object leaves the shard deleted and
            # nothing archived, and this is the only copy of a constituency that took an hour of
            # CPU and its share of $148 of Vision to produce.
            if _run("gcloud", "storage", "cp", source, target).returncode != 0:
                raise RuntimeError(f"could not archive {source}; refusing to delete it")
            _run("gcloud", "storage", "rm", source)
        moved.append(source)

    claim = f"{bucket}/claims/AC{ac_no:03d}.claim"
    if _run("gcloud", "storage", "ls", claim).returncode == 0:
        if not dry_run:
            _run("gcloud", "storage", "rm", claim)
        moved.append(claim)
    return moved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("acs", nargs="+", type=int)
    parser.add_argument("--bucket", default=BUCKET)
    parser.add_argument("--home", default="data/electors")
    parser.add_argument(
        "--stamp", required=True, help="archive folder name, e.g. 2026-08-13-bengali"
    )
    parser.add_argument("--yes", action="store_true", help="actually do it; otherwise a dry run")
    args = parser.parse_args()

    home = Path(args.home)
    print(f"{'HANDING BACK' if args.yes else 'DRY RUN'} -- archive under {ARCHIVE}/{args.stamp}\n")
    floors = {}
    for ac_no in args.acs:
        floors[ac_no] = rows_now(ac_no, home)
        moved = hand_back(ac_no, args.bucket, args.stamp, dry_run=not args.yes)
        print(f"   AC{ac_no:<5} {floors[ac_no]:>8,} rows now   {len(moved)} objects moved/cleared")

    print("\n   row-count floors for the re-run (it must not come back under these):")
    print("   " + ", ".join(f"AC{k}={v:,}" for k, v in sorted(floors.items())))
    if not args.yes:
        print("\n   re-run with --yes to actually hand these back")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
