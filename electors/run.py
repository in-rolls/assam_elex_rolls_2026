"""The chain, per constituency, so that changing the AC number is the only change.

    zip -> stage 1 (CPU: render, boxes, lines, repack, tesseract side-reads)
        -> stage 2 (Cloud Vision over the composites, words mapped back to boxes)
        -> a Parquet shard, appended to a manifest that accumulates across constituencies
        -> reconciled against each part's own printed closing total

Two properties are worth more than the code that provides them.

**A killed run never pays twice.** Stage one is resumable because its output is on disk and
``stage1.done`` can see it. Stage two is resumable because each part's rows are cached to
``rows.jsonl`` the moment Vision answers -- before anything is aggregated, written or reconciled.
Vision costs money and a crash between the API call and the shard would otherwise re-buy every
image in the constituency.

**Reconciliation is not optional and not silent.** Every part is scored against the roll's own
printed ``মূল তালিকা`` total -- the only number here that this pipeline did not itself produce --
and a part whose closing page could not be read is reported as *unmeasured* rather than counted
as passing. Those are different claims and the difference is the whole value of the check.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from assam_rolls import render

from . import output, pages, stage1, stage2, vision

#: Cached rows for one part, so a resumed run re-reads them instead of re-buying them.
ROWS = "rows.jsonl"


@dataclass
class ACRun:
    """What one constituency cost and what it established."""

    ac_no: int
    parts: int = 0
    boxes: int = 0
    rows: int = 0
    images_billed: int = 0
    parts_measured: int = 0
    parts_matching: int = 0
    residuals: List[Dict[str, Any]] = field(default_factory=list)
    failed_parts: List[Dict[str, str]] = field(default_factory=list)
    stage1_seconds: float = 0.0
    stage2_seconds: float = 0.0
    shard: str = ""
    error: str = ""

    @property
    def cost(self) -> float:
        return vision.cost(self.images_billed)

    def as_manifest_entry(self) -> Dict[str, Any]:
        return {
            "rows": self.rows,
            "parts": self.parts,
            "parts_measured": self.parts_measured,
            "parts_matching_roll": self.parts_matching,
            "parts_failed": len(self.failed_parts),
            "images_billed": self.images_billed,
            "vision_usd": round(self.cost, 2),
            "stage1_seconds": round(self.stage1_seconds, 1),
            "stage2_seconds": round(self.stage2_seconds, 1),
        }


def prepare(
    zip_path: Path,
    work_dir: Path,
    limit: Optional[int] = None,
    workers: int = 4,
    on_part: Optional[Callable[[stage1.PartPrep], None]] = None,
) -> List[stage1.PartPrep]:
    """Stage one over every part of one zip, skipping parts already prepared."""
    refs = list(render.iter_zip_parts(zip_path))
    if limit:
        refs = refs[:limit]
    work_dir.mkdir(parents=True, exist_ok=True)

    todo = [r for r in refs if not stage1.done(work_dir, r.part_no)]
    preps: List[stage1.PartPrep] = []
    if todo:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(stage1.prepare_part, zip_path, ref.pdf_name, ref.part_no, work_dir): ref
                for ref in todo
            }
            for future in as_completed(futures):
                prep = future.result()
                preps.append(prep)
                if on_part:
                    on_part(prep)
    return preps


def read(
    work_dir: Path,
    api_key: str,
    parts: Optional[Sequence[int]] = None,
    on_part: Optional[Callable[[int, int, int], None]] = None,
) -> List[Dict[str, Any]]:
    """Stage two over every prepared part, using cached rows where they exist.

    The cache is written per part rather than per constituency because that is the unit of
    money: a part read is a part paid for, and it should stay read.
    """
    rows: List[Dict[str, Any]] = []
    wanted = set(parts) if parts else None
    for part_dir in sorted(work_dir.glob("part*")):
        number = int(part_dir.name.replace("part", ""))
        if wanted is not None and number not in wanted:
            continue
        cache = part_dir / ROWS
        if cache.exists():
            found = [json.loads(line) for line in cache.read_text(encoding="utf-8").splitlines()]
            billed = 0
        else:
            if not stage2.ready(part_dir):
                continue
            result = stage2.read_part(part_dir, api_key)
            if result.error:
                raise RuntimeError(f"part {number}: {result.error}")
            found, billed = result.electors, result.images_billed
            _write_rows(cache, found)
        rows.extend(found)
        if on_part:
            on_part(number, len(found), billed)
    return rows


def _write_rows(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    """Atomically, so a kill mid-write cannot leave a half-part that looks complete."""
    temporary = path.with_suffix(".jsonl.tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    temporary.replace(path)


def images_billed(work_dir: Path) -> int:
    """What this constituency actually cost, counted from the composites on disk.

    Counted from the images rather than accumulated from the runs, because a resumed run bills
    nothing for the parts it skipped and the total would then shrink each time it was resumed.
    """
    return sum(len(stage2.composites(p)) for p in work_dir.glob("part*"))


def reconcile(work_dir: Path, rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Main-list rows against each part's own printed closing total.

    Main list only. A part's supplements are counted separately on the closing page, so scoring
    every row against that total makes a part with a supplement look over-extracted -- part 4
    read 511 boxes against a printed 483 and the 28 were its supplement, correctly found.
    """
    main_by_part: Dict[int, int] = {}
    for row in rows:
        if row.get("roll_section", pages.Section.MAIN.value) == pages.Section.MAIN.value:
            main_by_part[row["part_no"]] = main_by_part.get(row["part_no"], 0) + 1

    measured = matching = 0
    residuals: List[Dict[str, Any]] = []
    for part_dir in sorted(work_dir.glob("part*")):
        side_path = part_dir / "side.json"
        if not side_path.exists():
            continue
        number = int(part_dir.name.replace("part", ""))
        closing = stage1.read_side(side_path)["summary"]
        if closing is None:
            continue
        measured += 1
        got = main_by_part.get(number, 0)
        if got == closing.total:
            matching += 1
        else:
            residuals.append(
                {
                    "part_no": number,
                    "main_rows": got,
                    "roll_total": closing.total,
                    "diff": got - closing.total,
                }
            )
    return {"measured": measured, "matching": matching, "residuals": residuals}


def run_ac(
    zip_path: Path,
    work_dir: Path,
    api_key: str,
    shard_dir: Path = output.SHARD_DIR,
    manifest: Path = output.MANIFEST,
    limit: Optional[int] = None,
    workers: int = 4,
    log: Optional[Callable[[str], None]] = None,
) -> ACRun:
    """One constituency, end to end. The AC number is the only thing that changes."""
    say = log or (lambda _message: None)
    ac_no = _ac_number(zip_path)
    run = ACRun(ac_no=ac_no)

    started = time.time()
    preps = prepare(
        zip_path,
        work_dir,
        limit=limit,
        workers=workers,
        on_part=lambda p: say(
            f"    stage1 part {p.part_no:<5} {p.boxes:>5} boxes  {p.composites} images"
            + (f"  FAILED {p.error}" if p.error else "")
        ),
    )
    run.stage1_seconds = time.time() - started
    run.failed_parts = [{"part": str(p.part_no), "error": p.error} for p in preps if p.error]

    prepared = [p for p in sorted(work_dir.glob("part*")) if stage2.ready(p)]
    run.parts = len(prepared)
    run.boxes = sum(
        len(stage2.placements_of(p).get(c.name, [])) for p in prepared for c in stage2.composites(p)
    )
    say(f"    {run.parts} parts prepared, {run.boxes:,} boxes, {images_billed(work_dir)} images")

    started = time.time()
    try:
        rows = read(
            work_dir,
            api_key,
            on_part=lambda n, count, billed: say(
                f"    stage2 part {n:<5} {count:>5} rows"
                + ("  (cached)" if not billed else f"  {billed} images")
            ),
        )
    except RuntimeError as exc:
        run.error = str(exc)
        return run
    run.stage2_seconds = time.time() - started
    run.rows = len(rows)
    run.images_billed = images_billed(work_dir)

    # The shard is named from the archive; every row's ac_no comes from the PDF filename inside
    # it. They agree on real data, and if they ever do not, one of the two is wrong and the shard
    # would be filed under a constituency it does not contain. Cheap to check, silent if not.
    inside = {row["ac_no"] for row in rows}
    if inside and inside != {ac_no}:
        run.error = f"{zip_path.name} is AC{ac_no} but its rows carry {sorted(inside)}"
        return run

    found = reconcile(work_dir, rows)
    run.parts_measured = found["measured"]
    run.parts_matching = found["matching"]
    run.residuals = found["residuals"]

    path = output.write_shard(rows, ac_no, directory=shard_dir)
    run.shard = path.name
    output.update_manifest(ac_no, path, run.as_manifest_entry(), manifest=manifest)
    return run


def _ac_number(zip_path: Path) -> int:
    """From the archive's own name, e.g. ``AC17_ASM.zip`` -> 17."""
    digits = "".join(c for c in zip_path.stem.split("_")[0] if c.isdigit())
    if not digits:
        raise ValueError(f"cannot read an AC number from {zip_path.name}")
    return int(digits)


def render_report(runs: Sequence[ACRun]) -> str:
    """The three things worth reading: what it found, what it cost, what it reconciles to."""
    ok = [r for r in runs if not r.error]
    rows = sum(r.rows for r in ok)
    images = sum(r.images_billed for r in ok)
    measured = sum(r.parts_measured for r in ok)
    matching = sum(r.parts_matching for r in ok)

    lines = [
        f"RUN over {len(runs)} constituencies",
        "",
        f"   rows                  {rows:,}",
        f"   parts                 {sum(r.parts for r in ok):,}",
        f"   images billed         {images:,}",
        f"   Cloud Vision          ${vision.cost(images):.2f}",
        "",
        f"   reconciled            {matching}/{measured} parts exact against their printed total",
    ]
    unmeasured = sum(r.parts for r in ok) - measured
    if unmeasured:
        lines.append(f"   unmeasured            {unmeasured} parts, closing page unreadable")
    residuals = [(r.ac_no, d) for r in ok for d in r.residuals]
    if residuals:
        lines.append("")
        lines.append("   parts not matching:")
        for ac_no, item in residuals[:12]:
            lines.append(
                f"      AC{ac_no} part {item['part_no']}: {item['main_rows']} rows "
                f"against {item['roll_total']} printed ({item['diff']:+d})"
            )
        if len(residuals) > 12:
            lines.append(f"      ... and {len(residuals) - 12} more")
    failed = [(r.ac_no, f) for r in ok for f in r.failed_parts]
    if failed:
        lines.append("")
        lines.append(f"   {len(failed)} parts failed in stage one:")
        for ac_no, item in failed[:8]:
            lines.append(f"      AC{ac_no} part {item['part']}: {item['error']}")
    for r in runs:
        if r.error:
            lines.append(f"   AC{r.ac_no} FAILED: {r.error}")
    return "\n".join(lines)
