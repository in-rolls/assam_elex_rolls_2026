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
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from assam_rolls import render

from . import crops, output, pages, stage1, stage2, vision

#: Cached rows for one part, so a resumed run re-reads them instead of re-buying them.
ROWS = "rows.jsonl"

#: Concurrent Cloud Vision requests. The API allows 1,800 a minute and one composite takes about
#: nine seconds, so this is nowhere near the service's limit -- it is chosen to stay well inside
#: it while cutting the state's wait from ten days to under a day.
VISION_WORKERS = 24


@dataclass
class ACRun:
    """What one constituency cost and what it established."""

    ac_no: int
    parts: int = 0
    #: How many parts the info pages say this constituency has. The one number that would have
    #: caught AC101 shipping 113 of 210 the moment it happened, rather than an hour later when
    #: somebody noticed a coverage figure of 173%.
    parts_published: int = 0
    boxes: int = 0
    rows: int = 0
    images_billed: int = 0
    parts_measured: int = 0
    parts_matching: int = 0
    residuals: List[Dict[str, Any]] = field(default_factory=list)
    #: Rows the roll's own printed totals say are missing. Published constituencies are
    #: allowed a small, structural-loss-free shortfall, so the figure has to travel with the
    #: data rather than only appearing in a log nobody keeps.
    rows_short: int = 0
    #: Watermarked deletions detected, against what the roll's own arithmetic implies.
    struck_off: Dict[str, Any] = field(default_factory=dict)
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
            "parts_published": self.parts_published,
            "parts_measured": self.parts_measured,
            "parts_matching_roll": self.parts_matching,
            "rows_short_of_printed": self.rows_short,
            "parts_failed": len(self.failed_parts),
            "struck_off_detected": self.struck_off.get("detected"),
            "struck_off_implied": self.struck_off.get("implied"),
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
    workers: int = VISION_WORKERS,
) -> List[Dict[str, Any]]:
    """Stage two over every prepared part, using cached rows where they exist.

    The cache is written per part rather than per constituency because that is the unit of
    money: a part read is a part paid for, and it should stay read.

    Parts are read **concurrently**, because this stage does not compute anything -- it waits.
    Measured sequentially: 8.7 seconds an image, a load average of 0.8 on 32 cores, and 239
    hours for the state's 99,000 images. That is ten days of a machine being billed to hold a
    socket open, more than the API calls themselves cost. Cloud Vision allows 1,800 requests a
    minute; one at a time uses seven.

    Threads rather than processes: the work is a network round trip, so the interpreter lock is
    never the constraint, and each part writes only its own files.
    """
    wanted = set(parts) if parts else None
    todo = []
    for part_dir in sorted(work_dir.glob("part*")):
        number = int(part_dir.name.replace("part", ""))
        if wanted is not None and number not in wanted:
            continue
        todo.append((number, part_dir))

    found: Dict[int, List[Dict[str, Any]]] = {}
    errors: List[str] = []
    unready: List[int] = []

    def one(number: int, part_dir: Path) -> None:
        cache = part_dir / ROWS
        if cache.exists():
            rows = [json.loads(line) for line in cache.read_text(encoding="utf-8").splitlines()]
            billed = 0
        else:
            if not stage2.ready(part_dir):
                # Never silent. A part prepared but unreadable is the shape AC101 took when it
                # lost 97 parts, and a bare continue is what let it look like success.
                unready.append(number)
                return
            result = stage2.read_part(part_dir, api_key)
            if result.error:
                errors.append(f"part {number}: {result.error}")
                return
            rows, billed = result.electors, result.images_billed
            _write_rows(cache, rows)
        found[number] = rows
        if on_part:
            on_part(number, len(rows), billed)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for future in as_completed([pool.submit(one, n, d) for n, d in todo]):
            future.result()

    if unready:
        raise RuntimeError(
            f"{len(unready)} parts have stage-one metadata but no composites to read: "
            f"{sorted(unready)[:8]}. They were prepared by a machine whose disk is gone; "
            f"delete their part directories so they are prepared again."
        )
    if errors:
        raise RuntimeError("; ".join(errors[:5]))
    # Reassembled in part order. Concurrency decides when a part is read, never where its rows
    # land, and a shard whose row order depends on scheduling is not reproducible.
    return [row for number in sorted(found) for row in found[number]]


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


def _write_rows(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    """Atomically, so a kill mid-write cannot leave a half-part that looks complete."""
    temporary = path.with_suffix(".jsonl.tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
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
    # Biggest discrepancy first. They used to be reported in part order under the word "worst",
    # which made a 3-row shortfall and a lost page look alike in the log -- and the difference
    # between those two is the whole question.
    residuals.sort(key=lambda r: (-abs(r["diff"]), r["part_no"]))
    return {
        "measured": measured,
        "matching": matching,
        "residuals": residuals,
        "main_rows": sum(main_by_part.values()),
        "short": sum(abs(r["diff"]) for r in residuals),
        "worst": max((abs(r["diff"]) for r in residuals), default=0),
    }


#: A page of the roll holds thirty boxes, so anything structural that goes missing -- a tile, a
#: page, a part -- takes at least thirty rows with it. A part may fall short by less than that
#: without the constituency being wrong, and the gap between the two is where this line sits.
#: Deliberately well under thirty, so no lost page can hide beneath it.
MAX_PART_SHORTFALL = 5

#: And across the constituency, because many small shortfalls are not automatically forgivable.
MAX_TOTAL_SHORTFALL = 0.001


def shortfall_verdict(found: Dict[str, Any]) -> str:
    """Why this constituency may not be published, or "" if it may.

    Publishing is not the default outcome. The audit's blunt summary of this layer was that
    run_ac "records failed checks but publishes anyway" -- reconciliation was computed, stored,
    and then ignored by the line that writes the data.

    The correction over-shot. Requiring *every* measured part to match its printed total exactly
    made one missing row fatal to a whole constituency, and the arithmetic of that is brutal: at
    a 99.4% per-part match rate, a 232-part constituency publishes about a quarter of the time.
    AC8 read 231,131 rows, missed 28 of them, and was discarded -- 0.012% wrong, 100% thrown away.
    Fifty constituencies done and the queue not moving is what that policy looks like from
    outside.

    So the test is no longer "is anything missing" but "is what is missing structural". The known
    cause of the small shortfalls is a sparse final page -- a part whose last main-list page holds
    two or three electors gets classified unknown or missed entirely, and every extracted count
    involved is an exact multiple of the thirty boxes a page holds. That is a bounded, understood,
    recorded defect. A lost tile or page is none of those things, and is at least thirty rows, so
    it still fails here.

    The shortfall is not forgiven, only tolerated: it is recorded per constituency and carried
    into the verification bundle, so the published figure always says how far off the roll's own
    arithmetic it is.
    """
    worst, short, rows = found["worst"], found["short"], found["main_rows"]
    if worst > MAX_PART_SHORTFALL:
        return (
            f"a part is off by {worst} rows, more than the {MAX_PART_SHORTFALL} a marginal miss "
            f"can explain -- this is structural; worst {found['residuals'][:2]}"
        )
    allowed = rows * MAX_TOTAL_SHORTFALL
    if short > allowed:
        return (
            f"{short} rows short of the printed totals across {len(found['residuals'])} parts, "
            f"over the {allowed:,.0f} ({MAX_TOTAL_SHORTFALL:.1%}) this constituency allows"
        )
    return ""


def struck_off_check(
    work_dir: Path, rows: Sequence[Dict[str, Any]], published: Dict[int, int]
) -> Dict[str, Any]:
    """What the roll's own arithmetic says was struck off, against what was detected.

    A second route to the same number, sharing no code with the first. The detector reads a
    watermark off the pixels; this derives the count from two numbers the publisher printed on
    different pages and that this pipeline did not produce::

        info-page net = main-list total + additions - struck off

    So ``main + additions - net`` is how many entries should carry the stamp. Over the first 19
    parts of AC1 that is 514, and part 4's 14 was confirmed by hand.

    This matters because the watermark is the one field with no other check on it. A detector
    that silently found nothing would look exactly like a roll with no deletions, and every
    row would be published as a live elector. ``published`` maps part number to the info page's
    ``electors_total``, from ``dataset/parts.jsonl.gz``.
    """
    counted: Dict[int, Dict[str, int]] = {}
    for row in rows:
        part = counted.setdefault(row["part_no"], {"main": 0, "extra": 0, "deleted": 0})
        if row.get("roll_section", pages.Section.MAIN.value) == pages.Section.MAIN.value:
            part["main"] += 1
        else:
            part["extra"] += 1
        if row.get("deleted"):
            part["deleted"] += 1

    per_part: List[Dict[str, Any]] = []
    for part_no, found in sorted(counted.items()):
        net = published.get(part_no)
        if net is None:
            continue
        implied = found["main"] + found["extra"] - net
        per_part.append(
            {
                "part_no": part_no,
                "detected": found["deleted"],
                "implied": implied,
                "diff": found["deleted"] - implied,
            }
        )
    return {
        "parts": len(per_part),
        "detected": sum(p["detected"] for p in per_part),
        "implied": sum(p["implied"] for p in per_part),
        "per_part": per_part,
    }


def published_totals(ac_no: int, dataset: Path = Path("dataset/parts.jsonl.gz")) -> Dict[int, int]:
    """Each part's published net elector count, from the info pages already extracted."""
    import gzip

    if not dataset.exists():
        return {}
    out: Dict[int, int] = {}
    with gzip.open(dataset, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("ac_no") == ac_no and row.get("electors_total") is not None:
                out[row["part_no"]] = row["electors_total"]
    return out


def verification_bundle(work_dir: Path, rows: Sequence[Dict[str, Any]], published: int) -> Dict:
    """Everything needed to re-judge a constituency, in a few kilobytes.

    A verdict needs each part's printed closing total and how many boxes stage one found. Those
    live in ``side.json`` and ``manifest.jsonl``, which together are 412 MB a constituency --
    fifty gigabytes to check seven hundred megabytes of rows, and all of it in artefacts we
    intend to delete.

    So the numbers travel with the shard instead. The point is not convenience: it is that the
    check must still be runnable after the bucket is gone, by somebody who has only what was
    published.
    """
    totals: Dict[int, int] = {}
    boxes: Dict[int, int] = {}
    for part_dir in sorted(work_dir.glob("part*")):
        number = int(part_dir.name.replace("part", ""))
        side_path = part_dir / "side.json"
        if side_path.exists():
            closing = stage1.read_side(side_path)["summary"]
            if closing is not None:
                totals[number] = closing.total
        manifest = part_dir / crops.MANIFEST
        if manifest.exists():
            boxes[number] = sum(1 for _ in manifest.open(encoding="utf-8"))
    return {
        "published_parts": published,
        "printed_totals": {str(k): v for k, v in sorted(totals.items())},
        "boxes_per_part": {str(k): v for k, v in sorted(boxes.items())},
        "rows": len(rows),
    }


def run_ac(
    zip_path: Path,
    work_dir: Path,
    api_key: str,
    shard_dir: Path = output.SHARD_DIR,
    manifest: Path = output.MANIFEST,
    limit: Optional[int] = None,
    workers: int = 4,
    vision_workers: int = VISION_WORKERS,
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
            workers=vision_workers,
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

    # Against the info pages, which are not this pipeline's work. A constituency short of its
    # own published part count has lost whole parts, and that is not a quality question to be
    # noticed later -- it is a failed run.
    published = published_totals(ac_no)
    run.parts_published = len(published)
    if published and run.parts < len(published) * 0.99:
        run.error = (
            f"extracted {run.parts} parts of {len(published)} published -- "
            f"{len(published) - run.parts} are missing"
        )
        return run

    found = reconcile(work_dir, rows)
    run.parts_measured = found["measured"]
    run.parts_matching = found["matching"]
    run.residuals = found["residuals"]
    run.rows_short = found["short"]
    run.struck_off = struck_off_check(work_dir, rows, published_totals(ac_no))

    if found["measured"]:
        problem = shortfall_verdict(found)
        if problem:
            run.error = problem
            return run

    path = output.write_shard(rows, ac_no, directory=shard_dir)
    run.shard = path.name
    _write(
        shard_dir / f"AC{ac_no:03d}.verify.json",
        verification_bundle(work_dir, rows, len(published)),
    )
    # Its own file, not a shared manifest: four machines writing one JSON lose each other's
    # entries with nothing raised. electors status merges them back.
    output.write_entry(ac_no, path, run.as_manifest_entry(), directory=shard_dir)
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

    short = [r for r in ok if r.parts_published and r.parts < r.parts_published]
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
    detected = sum(r.struck_off.get("detected", 0) for r in ok)
    implied = sum(r.struck_off.get("implied", 0) for r in ok)
    if implied or detected:
        lines.append(
            f"   struck off            {detected:,} detected, {implied:,} implied by the "
            f"roll's own arithmetic"
        )
        if implied and detected < implied * 0.8:
            lines.append("   ^^ the watermark is being missed; those rows publish as live electors")

    for r in short:
        lines.append(f"   AC{r.ac_no} SHORT: {r.parts} parts of {r.parts_published} published")

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
