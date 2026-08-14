"""Command line for the elector stage.

    python -m electors parse data/ac_rolls/AC1_ASM.zip     extract one AC
    python -m electors report out/electors/AC001.parquet   reconcile against the info pages

Parts are independent, so they run in a process pool. The unit of retry is the part too: a
part that fails leaves the rest of the AC intact and is listed rather than silently dropped.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from assam_rolls import cache, ocr, render, schema

from . import extract, output, replay, timing, validate


def _one_part(args) -> Dict[str, Any]:
    """Worker entry point. Top-level so it pickles; the engine is built per process.

    Each part is cached as soon as it is read, and a cached part is never re-read. A run
    over one constituency is hours long on a busy machine, and the first version wrote
    nothing until all 154 parts were done -- so an interruption at hour four discarded four
    hours. ``assam_rolls.cache`` already solved this for the info pages, atomically and keyed
    to the source bytes; this simply uses it.
    """
    zip_path, pdf_name, cache_dir, capture_lines, engine_name, api_key = args
    zip_path, cache_dir = Path(zip_path), Path(cache_dir)
    capture_lines = bool(capture_lines)
    # Timed here rather than between yields in the parent: a pool returns results in
    # submission order, so the gap between two yields is whatever the slowest earlier part
    # was still doing, not the cost of the part that just arrived.
    started = time.time()

    key = Path(pdf_name).stem
    pdf_sha256 = render.sha256_bytes(render.read_pdf_bytes(zip_path, pdf_name))
    entry = cache.read_entry(cache_dir, key)
    # ``cache.is_fresh`` checks the *info-page* pipeline version, which does not move when
    # this stage changes. Without the stage's own version a resume would happily serve
    # results produced by code since fixed -- here, parts extracted before partial last
    # pages were handled, which are short by a page.
    fresh = cache.is_fresh(entry, pdf_sha256) and (
        entry["row"].get("stage_version") == extract.PIPELINE_VERSION
    )
    if capture_lines:
        # Capturing needs the OCR to actually run, so a part whose lines are not on disk is
        # re-read even when its rows are cached. Serving the row cache unconditionally here
        # would leave the line cache permanently empty on any machine that had run before.
        meta = schema.parse_source_filename(pdf_name) or {}
        on_disk = replay.path_for(meta.get("part_no", 0), meta.get("ac_no", 0)).exists()
        fresh = fresh and on_disk
    if fresh:
        return dict(entry["row"], electors=entry["sections"], cached=True, seconds=None)

    if engine_name == "vision":
        from . import vision_part

        result = vision_part.read_part(
            zip_path,
            pdf_name,
            api_key=api_key,
            renderer=vision_part.rasterize(extract.DPI),
        )
    else:
        engine = ocr.get_engine("tesseract", lang="asm")
        result = extract.read_part(zip_path, pdf_name, engine=engine, capture_lines=capture_lines)
    summary = {
        "ac_no": result.ac_no,
        "part_no": result.part_no,
        # The roll's own closing totals. Cached with the part, because they are what the
        # extracted rows are measured against.
        "summary_male": result.summary_male,
        "summary_female": result.summary_female,
        "summary_third": result.summary_third,
        "summary_total": result.summary_total,
        "page_count": result.page_count,
        "elector_pages": result.elector_pages,
        "unknown_pages": result.unknown_pages,
        "supplement_pages": result.supplement_pages,
        "error": result.error,
        "stage_version": extract.PIPELINE_VERSION,
        # What the part cost, in the unit the invoice is in. Zero for tesseract.
        "images_billed": result.images_billed,
        "engine": engine_name,
        "cached": False,
    }
    if not result.error:
        cache.write_entry(cache_dir, key, summary, result.electors, pdf_sha256)
    return dict(summary, electors=result.electors, seconds=time.time() - started)


def cmd_parse(args: argparse.Namespace) -> int:
    zip_path = Path(args.zip)
    if not zip_path.exists():
        print(f"no such zip: {zip_path}", file=sys.stderr)
        return 1
    render.require_poppler()

    parts = [p for p in render.iter_zip_parts(zip_path)]
    if args.limit:
        parts = parts[: args.limit]
    if not parts:
        print(f"no recognisable part PDFs in {zip_path}", file=sys.stderr)
        return 1

    api_key = ""
    if args.engine == "vision":
        import os

        api_key = args.api_key or os.environ.get("VISION_API_KEY", "")
        if not api_key:
            print("vision needs a key: --api-key or VISION_API_KEY", file=sys.stderr)
            return 1

    ac_no = parts[0].ac_no
    # One cache per engine. Keyed on the AC alone, a Vision run would have been served rows
    # tesseract produced -- the same part number, the same source bytes, the same stage
    # version, and completely different text.
    cache_dir = Path(args.cache) / f"AC{ac_no:03d}"
    if args.engine != "tesseract":
        cache_dir = cache_dir.with_name(f"{cache_dir.name}-{args.engine}")
    already = len(list(cache_dir.glob("*.json"))) if cache_dir.exists() else 0
    log = timing.setup(f"parse-AC{ac_no:03d}")
    log.info(
        "%s: %d parts, AC %d, %d workers%s%s",
        zip_path.name,
        len(parts),
        ac_no,
        args.workers,
        f", {already} already cached" if already else "",
        ", capturing OCR text" if args.capture else "",
    )
    clock = timing.RunClock(total=len(parts), workers=args.workers)

    rows: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        payload = [
            (
                str(zip_path),
                p.pdf_name,
                str(cache_dir),
                args.capture,
                args.engine,
                api_key,
            )
            for p in parts
        ]
        # Reported as each part *finishes*, not in submission order. `pool.map` yields in
        # order, so one slow part at the front holds back every result behind it: parts 3 and
        # 5 were on disk twenty minutes before the log mentioned either, because part 1 was
        # still going. Order is nothing to us -- every row carries its own part number.
        for result in (
            future.result()
            for future in as_completed([pool.submit(_one_part, item) for item in payload])
        ):
            done += 1
            if result["error"] or result["unknown_pages"]:
                failures.append(result)
            rows.extend(result["electors"])
            results.append(result)
            # Every part, not every tenth. A run that reports only every tenth part gives no
            # sign of life for its first hour, which is exactly when you need to know
            # whether it is working or wedged.
            clock.record(result.get("seconds"), bool(result.get("cached")))
            log.info(
                "%s | %s rows, %d with problems",
                clock.progress(
                    result["part_no"],
                    len(result["electors"]),
                    result.get("seconds"),
                    bool(result.get("cached")),
                ),
                f"{len(rows):,}",
                len(failures),
            )
            if result.get("error"):
                log.warning("part %s failed: %s", result["part_no"], result["error"])
            elif result.get("unknown_pages"):
                log.warning(
                    "part %s: %d pages whose geometry did not resolve: %s",
                    result["part_no"],
                    len(result["unknown_pages"]),
                    result["unknown_pages"][:8],
                )

    if not rows:
        print("no electors extracted", file=sys.stderr)
        return 1

    totals = validate.load_part_totals()
    roll_totals = {
        (r["ac_no"], r["part_no"]): {
            "total": r.get("summary_total"),
            "male": r.get("summary_male"),
            "female": r.get("summary_female"),
        }
        for r in results
        if r.get("summary_total")
    }
    checks = validate.reconcile(rows, totals, roll_totals)
    summary = validate.summarize(checks, rows)

    from . import vision as vision_engine

    billed = sum(r.get("images_billed") or 0 for r in results)
    dollars = vision_engine.cost(billed)

    path = output.write_shard(rows, ac_no, Path(args.out))
    entry = output.update_manifest(
        ac_no,
        path,
        {
            "rows": summary["rows"],
            "supplement_rows": summary["supplement_rows"],
            "parts_matching_roll": summary["parts_matching_roll"],
            "parts_measured": summary["parts_measured"],
            "male_share": summary["male_share"],
            "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            # What the constituency cost, so the remaining 125 can be planned rather than
            # guessed at. A run abandoned earlier at "36 hours" had that figure from watching
            # a terminal.
            "seconds": round(clock.elapsed, 1),
            "seconds_per_part": (
                round(sum(clock.worked) / len(clock.worked), 1) if clock.worked else None
            ),
            "parts_from_cache": clock.cached,
            "workers": args.workers,
            "engine": args.engine,
            # The bill, in the unit it arrives in. Recorded per constituency so the remaining
            # 125 are a multiplication rather than an estimate.
            "images_billed": billed,
            "dollars": round(dollars, 4),
        },
    )

    for line in clock.summary():
        log.info("%s", line)
    if billed:
        log.info("%d images billed = $%.4f for AC %d", billed, dollars, ac_no)
    print(f"\nwrote {path} ({entry['bytes'] / 1e6:.1f} MB)")
    print(f"rows: {summary['rows']:,} ({summary['supplement_rows']:,} from supplements)")
    print(
        f"against the roll's own total: {summary['parts_matching_roll']}"
        f"/{summary['parts_measured']} parts exact "
        f"({summary['parts_matching_roll_rate']:.1%})"
    )
    if summary["roll_male_share"] is not None:
        print(
            f"male share: {summary['male_share']:.1%} extracted vs "
            f"{summary['roll_male_share']:.1%} on the roll's own summary"
        )
    print(
        f"rows whose OCR'd serial disagrees with its position: "
        f"{summary['rows_with_serial_disagreement']:,}"
    )
    if summary["parts_unmeasured"]:
        print(
            f"{len(summary['parts_unmeasured'])} parts whose closing total could not be read "
            f"(excluded, not guessed): {summary['parts_unmeasured'][:10]}"
        )
    if summary["roll_residuals"]:
        print(f"\n{len(summary['roll_residuals'])} parts not matching their roll total:")
        for r in summary["roll_residuals"][:10]:
            print(f"   part {r['part_no']}: {r['rows']} rows vs {r['roll_total']} ({r['diff']:+d})")
    _print_fields(summary["fields"])
    if failures:
        print(f"\n{len(failures)} parts with unreadable pages or errors:")
        for f in failures[:10]:
            print(f"   part {f['part_no']}: unknown_pages={f['unknown_pages']} {f['error']}")
    return 0


def _print_fields(report: Dict[str, Any]) -> None:
    print("\nfield fill rates:")
    for key in (
        "epic_present",
        "epic_well_formed",
        "name_present",
        "relation_present",
        "house_present",
        "age_present",
        "sex_present",
        "needs_review",
    ):
        print(f"   {key:<20} {report[key]:>6.1%}")
    if report["flags"]:
        print("   flags:", dict(sorted(report["flags"].items(), key=lambda kv: -kv[1])))


def cmd_quality(args: argparse.Namespace) -> int:
    """Extract a random sample of parts and report what can be established about it."""
    import random

    from . import quality

    zip_path = Path(args.zip)
    if not zip_path.exists():
        print(f"no such zip: {zip_path}", file=sys.stderr)
        return 1
    render.require_poppler()

    everything = list(render.iter_zip_parts(zip_path))
    if not everything:
        print(f"no recognisable part PDFs in {zip_path}", file=sys.stderr)
        return 1
    # Seeded, so a re-measure after a fix is comparable rather than a different sample.
    chosen = random.Random(args.seed).sample(everything, min(args.parts, len(everything)))
    ac_no = chosen[0].ac_no
    cache_dir = Path(args.cache) / f"AC{ac_no:03d}"
    print(
        f"{zip_path.name}: sampling {len(chosen)} of {len(everything)} parts "
        f"(seed {args.seed}), {args.workers} workers"
    )

    rows: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        payload = [
            (str(zip_path), p.pdf_name, str(cache_dir), False, "tesseract", "") for p in chosen
        ]
        for done, result in enumerate(pool.map(_one_part, payload, chunksize=1), start=1):
            rows.extend(result["electors"])
            results.append(result)
            print(
                f"  {done}/{len(chosen)} part {result['part_no']}: "
                f"{len(result['electors'])} electors | {len(rows):,} total"
                + (" (cached)" if result.get("cached") else ""),
                flush=True,
            )

    if not rows:
        print("no electors extracted", file=sys.stderr)
        return 1

    roll_totals = {
        (r["ac_no"], r["part_no"]): {
            "total": r.get("summary_total"),
            "male": r.get("summary_male"),
            "female": r.get("summary_female"),
        }
        for r in results
        if r.get("summary_total")
    }
    checks = validate.reconcile(rows, validate.load_part_totals(), roll_totals)
    data = quality.report(rows, validate.summarize(checks, rows))

    print()
    print(quality.format_report(data))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


def cmd_diagnose(args: argparse.Namespace) -> int:
    """Where the failures concentrate, what to try, and what to work on first."""
    from . import diagnose

    rows, _ = _cached(Path(args.cache), args.ac)
    rows = diagnose.derive_features(rows)
    if not rows:
        print(
            f"no cached parts under {args.cache}; run `quality` or `parse` first",
            file=sys.stderr,
        )
        return 1

    print(f"{len(rows):,} rows from cache\n")
    print(diagnose.render_priorities(diagnose.priorities(rows)))
    found = diagnose.associations(rows)
    print()
    print(diagnose.render(found, diagnose.proposals(found)))
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    """Gate against a git revision, or record and compare a baseline file."""
    from . import bench, quality

    if args.against:
        return _bench_against(args)

    rows, roll_totals = _cached(Path(args.cache), args.ac)
    if not rows:
        print(f"no cached parts under {args.cache}; run `quality` first", file=sys.stderr)
        return 1

    parts = sorted({r["part_no"] for r in rows})
    splits = bench.split_parts(parts)
    current: Dict[str, Any] = {}
    for name, members in splits.items():
        subset = [r for r in rows if r["part_no"] in set(members)]
        if not subset:
            continue
        checks = validate.reconcile(subset, validate.load_part_totals(), roll_totals)
        report = quality.report(subset, validate.summarize(checks, subset))
        current[name] = bench.metrics_from(report, validate.summarize(checks, subset))
    current["seconds_per_part"] = args.seconds

    if args.record:
        path = bench.save_baseline(current)
        print(f"recorded baseline for {len(current) - 1} splits -> {path}")
        for name, metrics in current.items():
            if isinstance(metrics, dict):
                print(f"   {name:<12} {metrics.get(args.target, float('nan')):.1%} {args.target}")
        return 0

    baseline = bench.load_baseline()
    if not baseline:
        print("no baseline recorded; run `bench --record` before the fix", file=sys.stderr)
        return 1
    print(bench.render(bench.compare(args.target, baseline, current)))
    return 0


def _bench_against(args: argparse.Namespace) -> int:
    """Replay the line cache through a revision's parser and today's, and print the gates."""
    from . import against, bench

    parts = list(args.parts) if args.parts else replay.cached_parts(ac=args.ac)
    absent = replay.missing(parts, ac=args.ac)
    if absent:
        print(f"no capture for parts {absent} -- run `capture` first", file=sys.stderr)
        parts = [p for p in parts if p not in absent]
    if not parts:
        print("nothing captured to replay", file=sys.stderr)
        return 1

    _, roll_totals = _cached(Path(args.cache), args.ac)
    found = against.compare(
        args.against,
        parts,
        roll_totals,
        target=args.target,
        higher_is_better=args.higher_is_better,
        ac=args.ac,
    )
    print(against.render(found, args.target))
    return 0 if bench.verdict(found["gates"]) else 1


def _cached(cache_root: Path, ac_no: int):
    """Every extracted elector for this AC, **and** each part's own roll totals.

    Both, because the totals are what the guarded ground-truth metrics are computed from.
    Returning rows alone would leave completeness and the sex ratio absent from the gate,
    and ``gate_no_damage`` skips metrics it cannot find on both sides -- so the two checks
    that matter most would have passed by being missing.
    """
    directory = cache_root / f"AC{ac_no:03d}"
    rows: List[Dict[str, Any]] = []
    totals: Dict[tuple, Dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        rows.extend(entry.get("sections") or [])
        summary = entry.get("row") or {}
        if summary.get("summary_total"):
            totals[(summary.get("ac_no"), summary.get("part_no"))] = {
                "total": summary["summary_total"],
                "male": summary.get("summary_male"),
                "female": summary.get("summary_female"),
            }
    return rows, totals


def cmd_capture(args: argparse.Namespace) -> int:
    """Run the real pipeline once and write down every line the OCR produced.

    The point is everything that comes after: a parsing change can then be scored against
    identical text in seconds. Costs one JSON file per part and no extra OCR.
    """
    zip_path = Path(args.zip)
    if not zip_path.exists():
        print(f"no such zip: {zip_path}", file=sys.stderr)
        return 1
    render.require_poppler()

    everything = list(render.iter_zip_parts(zip_path))
    wanted = set(args.parts) if args.parts else None
    chosen = [p for p in everything if wanted is None or p.part_no in wanted]
    if not chosen:
        print("no matching parts", file=sys.stderr)
        return 1

    ac_no = chosen[0].ac_no
    cache_dir = Path(args.cache) / f"AC{ac_no:03d}"
    print(f"{zip_path.name}: capturing lines for {len(chosen)} parts, {args.workers} workers")

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        payload = [
            (str(zip_path), p.pdf_name, str(cache_dir), True, "tesseract", "") for p in chosen
        ]
        for done, result in enumerate(pool.map(_one_part, payload, chunksize=1), start=1):
            print(
                f"  {done}/{len(chosen)} part {result['part_no']}: "
                f"{len(result['electors'])} electors captured",
                flush=True,
            )

    captured = replay.cached_parts(ac=ac_no)
    print(f"\n{len(captured)} parts in the line cache at {replay.CACHE_DIR}")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """Re-parse cached OCR text with today's code and report what it produces.

    Instant, and exact for any change downstream of the text. It says nothing about a change
    to the crop geometry or the engine -- those change the text itself, and this would replay
    the old text and report, truthfully but uselessly, that nothing moved.
    """
    from . import diagnose, quality

    parts = list(args.parts) if args.parts else replay.cached_parts(ac=args.ac)
    absent = replay.missing(parts, ac=args.ac)
    if absent:
        print(f"no capture for parts {absent} -- run `capture` first", file=sys.stderr)
        parts = [p for p in parts if p not in absent]
    if not parts:
        return 1

    rows = replay.replay_parts(parts, ac=args.ac)
    print(f"replayed {len(rows):,} rows from {len(parts)} parts\n")
    print(quality.format_report(quality.report(rows, {})))
    if args.diagnose:
        print()
        print(diagnose.render_priorities(diagnose.priorities(rows)))
    return 0


def cmd_escalate(args: argparse.Namespace) -> int:
    """How many rows the cheap pass wants a second opinion on, and why."""
    from . import escalate

    if args.replay:
        parts = list(args.parts) if args.parts else replay.cached_parts(ac=args.ac)
        rows = replay.replay_parts(parts, ac=args.ac)
    else:
        rows, _ = _cached(Path(args.cache), args.ac)
    if not rows:
        print("no rows: run `parse`, `quality` or `capture` first", file=sys.stderr)
        return 1

    print(escalate.report(rows))
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    """Write the shard from the line cache, re-parsing with today's code and no OCR.

    A parsing fix that lands mid-run would otherwise force the whole constituency to be read
    again -- eleven hours to change how a string is interpreted. The captured lines are the
    expensive asset; the rows are derived, so they are re-derived.
    """
    from . import quality

    parts = list(args.parts) if args.parts else replay.cached_parts(ac=args.ac)
    if not parts:
        print(
            f"no captured lines for AC {args.ac}; run `parse --capture` first",
            file=sys.stderr,
        )
        return 1

    log = timing.setup(f"rebuild-AC{args.ac:03d}", to_file=False)
    clock = timing.RunClock(total=len(parts))
    rows: List[Dict[str, Any]] = []
    for part in parts:
        captured = replay.load(part, ac=args.ac)
        if captured is None:
            continue
        part_rows = replay.replay(captured)
        rows.extend(part_rows)
        clock.record(0.0, was_cached=False)
        log.info(
            "%s | %s rows",
            clock.progress(part, len(part_rows), 0.0, False),
            f"{len(rows):,}",
        )

    if not rows:
        print("no rows rebuilt", file=sys.stderr)
        return 1

    _, roll_totals = _cached(Path(args.cache), args.ac)
    checks = validate.reconcile(rows, validate.load_part_totals(), roll_totals)
    summary = validate.summarize(checks, rows)
    path = output.write_shard(rows, args.ac, Path(args.out))
    output.update_manifest(
        args.ac,
        path,
        {
            "rows": summary["rows"],
            "supplement_rows": summary["supplement_rows"],
            "parts_matching_roll": summary["parts_matching_roll"],
            "parts_measured": summary["parts_measured"],
            "male_share": summary["male_share"],
            "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            # Named so a shard rebuilt from cached text is never mistaken for a fresh read.
            "rebuilt_from_lines": True,
            "parts": len(parts),
        },
    )
    for line in clock.summary():
        log.info("%s", line)
    print(f"\nwrote {path} from {len(parts)} captured parts, no OCR")
    print(quality.format_report(quality.report(rows, summary)))
    return 0


def cmd_second_pass(args: argparse.Namespace) -> int:
    """Re-read the boxes the cheap pass could not, with savitr's roll model.

    Only boxes missing a field this engine can supply are sent. It costs about six seconds a
    box against tesseract's 1.7, so asking it about a box we already answered is the one thing
    that makes the tier not worth having.
    """
    import tempfile

    from PIL import Image

    from . import grid, pages, second_pass

    zip_path = Path(args.zip)
    if not zip_path.exists():
        print(f"no such zip: {zip_path}", file=sys.stderr)
        return 1
    render.require_poppler()

    wanted_parts = set(args.parts or [])
    everything = [p for p in render.iter_zip_parts(zip_path) if p.part_no in wanted_parts]
    if not everything:
        print("no matching parts", file=sys.stderr)
        return 1

    log = timing.setup("second-pass", to_file=False)
    engine = second_pass.TerseEngine()
    clock = timing.RunClock(total=len(everything))
    before_all: List[Dict[str, Any]] = []
    after_all: List[Dict[str, Any]] = []

    try:
        for part in everything:
            captured = replay.load(part.part_no, ac=part.ac_no)
            if captured is None:
                log.warning("part %s has no captured lines; skipped", part.part_no)
                continue
            rows = replay.replay(captured)
            targets = {
                (r["page_no"], r["box_row"], r["box_col"]): i
                for i, r in enumerate(rows)
                if second_pass.wanted(r)
            }
            log.info(
                "part %s: %d of %d boxes want a second read",
                part.part_no,
                len(targets),
                len(rows),
            )
            if not targets:
                continue

            pdf_bytes = render.read_pdf_bytes(zip_path, part.pdf_name)
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)
                (work / "part.pdf").write_bytes(pdf_bytes)
                subprocess.run(
                    [
                        "pdftoppm",
                        "-gray",
                        "-r",
                        str(extract.DPI),
                        "-png",
                        str(work / "part.pdf"),
                        str(work / "page"),
                    ],
                    check=True,
                    capture_output=True,
                )
                for image_path in sorted(work.glob("page-*.png")):
                    number = int(image_path.stem.split("-")[-1])
                    if not any(key[0] == number for key in targets):
                        continue
                    image = Image.open(image_path)
                    signature = pages.classify(image, number)
                    if not signature.is_elector:
                        continue
                    for box in grid.build(signature.h_rules, signature.v_rules):
                        index = targets.get((number, box.row, box.column))
                        if index is None:
                            continue
                        crop = image.crop((box.left, box.top, box.text_right, box.bottom))
                        rows[index] = second_pass.merge(rows[index], engine.read(crop))
                    image.close()

            before_all.extend(replay.replay(captured))
            after_all.extend(rows)
            clock.record(0.0, was_cached=False)
            log.info("%s", clock.progress(part.part_no, len(rows), 0.0, False))
    finally:
        engine.close()

    if not after_all:
        print("nothing was re-read", file=sys.stderr)
        return 1
    print()
    print(second_pass.render(second_pass.summarise(before_all, after_all)))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    rows = output.read_shard(Path(args.shard))
    checks = validate.reconcile(rows, validate.load_part_totals())
    summary = validate.summarize(checks, rows)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def cmd_resolution(args: argparse.Namespace) -> int:
    """Run the same parts through Vision at several resolutions and compare."""
    import os

    from . import evaluate, resolution, vision

    if args.load or args.dots:
        from . import crops as crops_module

        loaded = evaluate.load(Path(args.truth)) if args.truth else None
        arms = resolution.load(Path(args.load)) if args.load else []
        if args.dots:
            # Readings from whatever GPU ran them, parsed with the same parser Vision's output
            # goes through. Two parsers for one comparison would measure the parsers.
            readings = json.loads(Path(args.dots).read_text(encoding="utf-8"))
            arms = arms + crops_module.readings_to_arm(readings)
        if not arms:
            print("nothing to compare", file=sys.stderr)
            return 1
        print(resolution.report(arms, loaded["truth"] if loaded else None))
        return 0

    api_key = args.api_key or os.environ.get("VISION_API_KEY", "")
    if not api_key:
        print("need a Cloud Vision key: --api-key or VISION_API_KEY", file=sys.stderr)
        return 1

    zip_path = Path(args.zip)
    if not zip_path.exists():
        print(f"no such zip: {zip_path}", file=sys.stderr)
        return 1
    render.require_poppler()

    loaded = evaluate.load(Path(args.truth)) if args.truth else None
    truth = loaded["truth"] if loaded else None

    parts = [p for p in render.iter_zip_parts(zip_path)]
    wanted = resolution.truth_parts(truth) if (truth and args.truth_parts) else {}
    if wanted:
        parts = [p for p in parts if p.part_no in wanted]
    else:
        parts = parts[: args.limit]
    if not parts:
        print("no parts selected", file=sys.stderr)
        return 1

    arms = [a.strip() for a in args.arms.split(",")] if args.arms else list(resolution.ARMS)
    unknown = [a for a in arms if a not in resolution.ARMS]
    if unknown:
        print(
            f"unknown arm(s): {unknown}; known: {list(resolution.ARMS)}",
            file=sys.stderr,
        )
        return 1

    log = timing.setup("resolution")
    log.info(
        "%d parts x %d arms (%s)%s",
        len(parts),
        len(arms),
        ", ".join(arms),
        f", stacking fixed at {args.stack}" if args.stack else "",
    )

    results: List[Any] = []
    for part in parts:
        for arm in arms:
            found = resolution.read_arm(
                zip_path,
                part.pdf_name,
                part_no=part.part_no,
                arm=arm,
                api_key=api_key,
                pages_per_image=args.stack or None,
            )
            results.append(found)
            log.info(
                "part %-4d %-8s %3d pages, %2d images, %4d boxes%s",
                part.part_no,
                arm,
                found.pages,
                found.images_billed,
                len(found.boxes),
                f"  ERROR {found.error}" if found.error else "",
            )

    billed = sum(r.images_billed for r in results)
    log.info("spent %d images = $%.4f", billed, vision.cost(billed))
    if args.save:
        # Written before the report, so an analysis bug never costs the requests again.
        log.info("readings saved to %s", resolution.save(results, Path(args.save)))
    print()
    print(resolution.report(results, truth))
    return 0


def cmd_crops(args: argparse.Namespace) -> int:
    """Cut box images out so a second reader can run them on a GPU somewhere else."""
    from . import crops

    zip_path = Path(args.zip)
    if not zip_path.exists():
        print(f"no such zip: {zip_path}", file=sys.stderr)
        return 1
    render.require_poppler()

    parts = [p for p in render.iter_zip_parts(zip_path)][: args.parts]
    out_dir = Path(args.out)
    log = timing.setup("crops")
    log.info("%d parts -> %s, at most %d crops", len(parts), out_dir, args.limit)

    total = 0
    for part_no, written in crops.export(zip_path, parts, out_dir, dpi=args.dpi, limit=args.limit):
        total += len(written)
        log.info("part %-4d %4d crops (%d total)", part_no, len(written), total)

    if not total:
        print("no crops written", file=sys.stderr)
        return 1

    size = sum(p.stat().st_size for p in out_dir.glob("*.png"))
    print(f"\n{total:,} crops in {out_dir} ({size / 1e6:.0f} MB)")
    if args.zip_to:
        target = crops.zip_dir(out_dir, Path(args.zip_to).resolve())
        print(f"zipped to {target} ({target.stat().st_size / 1e6:.0f} MB) -- upload as a dataset")
    print("the filename is the key: p<part>_pg<page>_r<row>c<col>.png")
    return 0


def _one_prep(args) -> Dict[str, Any]:
    """Worker for stage one. Top-level so it pickles."""
    from dataclasses import asdict

    from . import stage1

    zip_path, pdf_name, part_no, out_dir, dpi = args
    out = Path(out_dir)
    if stage1.done(out, part_no):
        return {
            "part_no": part_no,
            "cached": True,
            "boxes": 0,
            "composites": 0,
            "pages": 0,
            "elector_pages": 0,
            "unknown_pages": [],
            "summary_total": None,
            "seconds": 0.0,
            "error": "",
            "ac_no": 0,
        }
    prep = stage1.prepare_part(Path(zip_path), pdf_name, part_no, out, dpi=dpi)
    return dict(asdict(prep), cached=False)


def cmd_stage1(args: argparse.Namespace) -> int:
    """Render, find the boxes, repack them, and read what the CPU can. No API calls."""
    from . import stage1

    zip_path = Path(args.zip)
    if not zip_path.exists():
        print(f"no such zip: {zip_path}", file=sys.stderr)
        return 1
    render.require_poppler()

    parts = [p for p in render.iter_zip_parts(zip_path)]
    if args.limit:
        parts = parts[: args.limit]
    if not parts:
        print(f"no recognisable part PDFs in {zip_path}", file=sys.stderr)
        return 1

    ac_no = parts[0].ac_no
    out_dir = Path(args.out) / f"AC{ac_no:03d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    already = sum(1 for p in parts if stage1.done(out_dir, p.part_no))
    log = timing.setup(f"stage1-AC{ac_no:03d}")
    log.info(
        "%s: %d parts, AC %d, %d workers%s -> %s",
        zip_path.name,
        len(parts),
        ac_no,
        args.workers,
        f", {already} already prepared" if already else "",
        out_dir,
    )
    clock = timing.RunClock(total=len(parts), workers=args.workers)

    preps: List[Any] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        payload = [(str(zip_path), p.pdf_name, p.part_no, str(out_dir), args.dpi) for p in parts]
        for row in (f.result() for f in as_completed([pool.submit(_one_prep, i) for i in payload])):
            preps.append(stage1.PartPrep(**{k: v for k, v in row.items() if k != "cached"}))
            clock.record(row.get("seconds"), bool(row.get("cached")))
            log.info(
                "%s | %s boxes, %s composites%s",
                clock.progress(
                    row["part_no"],
                    row["boxes"],
                    row.get("seconds"),
                    bool(row.get("cached")),
                ),
                f"{row['boxes']:,}",
                row["composites"],
                f"  ERROR {row['error']}" if row["error"] else "",
            )

    found = stage1.summarise(preps)
    for line in clock.summary():
        log.info("%s", line)
    print(f"\nstage one for AC {ac_no} -> {out_dir}")
    print(
        f"  pages {found['pages']:,}   boxes {found['boxes']:,}   "
        f"composites {found['composites']:,}"
    )
    print(
        f"  against each part's own printed total: "
        f"{found['parts_matching_roll']}/{found['parts_measured']} exact"
    )
    if found["parts_unmeasured"]:
        print(
            f"  {len(found['parts_unmeasured'])} parts whose closing total could not be read "
            f"(excluded, not guessed): {found['parts_unmeasured'][:10]}"
        )
    if found["residuals"]:
        print(f"  {len(found['residuals'])} parts not matching their roll total:")
        for r in found["residuals"][:10]:
            print(
                f"     part {r['part_no']}: {r['main_boxes']} main boxes "
                f"(+{r['supplement_boxes']} supplement) vs {r['roll_total']} printed "
                f"({r['diff']:+d})"
            )
    if found["unknown_pages"]:
        print(f"  pages whose geometry did not resolve: {found['unknown_pages']}")
    if found["failed"]:
        print(f"  {found['failed']} parts failed")
    print(f"\n  Cloud Vision would then cost ${found['vision_cost']:.2f} for this constituency")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """The whole chain per constituency. Resumable, and it never pays for a part twice."""
    import os

    from . import run as runner
    from . import vision

    # Empty means application-default credentials, which is the preferred path -- on a VM that
    # is the attached service account and no secret exists to leak.
    api_key = "" if args.prepare_only else os.environ.get("VISION_API_KEY", "")
    if not args.prepare_only and not api_key:
        try:
            vision.bearer_token()
        except Exception as exc:
            print(
                f"no Cloud Vision credentials: {type(exc).__name__}: {exc}\n"
                "run 'gcloud auth application-default login', or set VISION_API_KEY, "
                "or pass --prepare-only to stop before Vision",
                file=sys.stderr,
            )
            return 1
    render.require_poppler()

    runs = []
    for name in args.zips:
        zip_path = Path(name)
        if not zip_path.exists():
            print(f"no such zip: {zip_path}", file=sys.stderr)
            return 1
        ac_no = runner._ac_number(zip_path)
        work = Path(args.work) / f"AC{ac_no:03d}"
        print(f"\nAC{ac_no}  {zip_path.name}")

        if args.prepare_only:
            runner.prepare(
                zip_path,
                work,
                limit=args.limit or None,
                workers=args.workers,
                on_part=lambda p: print(
                    f"    stage1 part {p.part_no:<5} {p.boxes:>5} boxes  {p.composites} images"
                    + (f"  FAILED {p.error}" if p.error else "")
                ),
            )
            images = runner.images_billed(work)
            print(f"    prepared. {images} images would cost ${vision.cost(images):.2f}")
            continue

        found = runner.run_ac(
            zip_path,
            work,
            api_key,
            shard_dir=Path(args.shards),
            manifest=Path(args.manifest),
            limit=args.limit or None,
            workers=args.workers,
            log=print,
        )
        runs.append(found)
        print(f"    {found.rows:,} rows -> {found.shard}  ${found.cost:.2f}")

    if runs:
        print()
        print(runner.render_report(runs))
    return 0 if all(not r.error for r in runs) else 1


#: Constituencies published in Bengali. The editions take different label paths through the
#: parser, so a single overall rate hides a failure confined to one of them -- which is exactly
#: how an empty house-number column survived in 2.2 million rows.
BENGALI = frozenset({114, 115, 116, 117, 118, 119, 120, 122, 123, 125, 126})


def cmd_floor(args: argparse.Namespace) -> int:
    """Count the rows that are wrong on their face, and say what the count does not cover."""
    from . import floor, output

    home = Path(args.home)
    shards = sorted(home.glob("AC*.parquet"), key=lambda p: int(p.name[2:5]))
    if args.ac:
        shards = [p for p in shards if int(p.name[2:5]) == args.ac]
    if not shards:
        print("   no shards; run 'electors pull' first")
        return 1

    groups: Dict[str, List[Dict[str, Any]]] = {"Assamese": [], "Bengali": []}
    for shard in shards:
        edition = "Bengali" if int(shard.name[2:5]) in BENGALI else "Assamese"
        groups[edition].extend(output.read_shard(shard))

    for edition, rows in groups.items():
        if rows:
            print(floor.render(floor.scan(rows), edition))
            print()
    print("   a lower bound. A row no detector condemns is unmeasured, not correct --")
    print("   nothing here can see a name that is plausible and wrong.")
    return 0


def cmd_fleet(args: argparse.Namespace) -> int:
    """Answer 'is everything I uploaded being worked on' without archaeology."""
    import shutil

    from . import fleet as fleet_module

    state = fleet_module.survey(args.bucket)
    if not state.constituencies:
        print("   nothing in the bucket -- is --bucket right, and is gcloud authenticated?")
        return 1
    free_gb = shutil.disk_usage(Path.cwd()).free / 1e9
    print(fleet_module.render(state, free_gb))
    return 0


def cmd_bundle(args: argparse.Namespace) -> int:
    """Give the already-finished constituencies something to be judged against."""
    from . import deliver

    home = Path(args.home)
    entries = json.loads(deliver.index_path(home).read_text(encoding="utf-8"))
    arrivals = []
    for key in sorted(entries, key=int):
        ac_no = int(key)
        if (home / f"AC{ac_no:03d}.verify.json").exists():
            continue
        print(f"   AC{ac_no}: fetching evidence")
        bundle = deliver.backfill_bundle(args.bucket, ac_no, home)
        if bundle is None:
            print("      no stage1 evidence in the bucket; cannot judge")
            continue
        judged = deliver.judge_local(ac_no, home)
        arrivals.append(judged)
        print(f"      {judged.rows:,} rows, {judged.verdict}")
        for reason in judged.reasons:
            print(f"      ! {reason}")
    if arrivals:
        deliver.record(arrivals, home)
    return 0


def cmd_reap(args: argparse.Namespace) -> int:
    """Free the bucket of archives we can prove we no longer need."""
    from . import deliver

    home = Path(args.home)
    safe = deliver.reapable(home)
    if not safe:
        print("nothing is safe to delete yet: an archive goes only when its constituency is")
        print("home, its bytes match, its verdict is PASS, and its Vision words are cached.")
        return 0
    print(f"safe to delete: {safe}")
    count = deliver.reap(args.bucket, home, dry_run=not args.yes, say=print)
    print(f"\n   {count} archives {'deleted' if args.yes else 'would be deleted'}")
    if not args.yes:
        print("   re-run with --yes to actually delete")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    """Bring finished constituencies home, checked on arrival."""
    from . import deliver

    home = Path(args.home)
    words = None if args.no_words else Path(args.words)
    if args.watch:
        print(f"watching {args.bucket} every {args.every}s -> {home}")
        deliver.watch(args.bucket, home, words, every=args.every, say=print)
        return 0
    arrivals = deliver.pull(args.bucket, home, words, say=print)
    if not arrivals:
        print("nothing new")
        return 0
    passed = sum(1 for a in arrivals if a.verdict == "PASS")
    print(f"\n   {len(arrivals)} constituencies pulled, {passed} pass")
    return 0 if passed == len(arrivals) else 1


def cmd_status(args: argparse.Namespace) -> int:
    """One verdict per constituency, from the shards and the roll's own numbers.

    Recomputed from the data rather than read out of a manifest, so it cannot report a pass that
    the current shards do not support.
    """
    import collections
    import gzip

    from . import reconcile, stage1

    published: Dict[int, int] = collections.Counter()
    male: Dict[int, int] = collections.Counter()
    female: Dict[int, int] = collections.Counter()
    info = Path(args.info)
    if info.exists():
        with gzip.open(info, "rt", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                published[row["ac_no"]] += 1
                male[row["ac_no"]] += row.get("electors_male") or 0
                female[row["ac_no"]] += row.get("electors_female") or 0

    verdicts = []
    for shard in sorted(Path(args.shards).glob("AC*.parquet")):
        ac_no = int(shard.stem[2:])
        rows = output.read_shard(shard)
        totals: Dict[int, int] = {}
        boxes: Dict[int, int] = {}
        work = Path(args.work) / f"AC{ac_no:03d}"
        for part_dir in sorted(work.glob("part*")):
            number = int(part_dir.name.replace("part", ""))
            side_path = part_dir / "side.json"
            if side_path.exists():
                closing = stage1.read_side(side_path)["summary"]
                if closing is not None:
                    totals[number] = closing.total
            manifest = part_dir / "manifest.jsonl"
            if manifest.exists():
                boxes[number] = sum(1 for _ in manifest.open(encoding="utf-8"))
        verdicts.append(
            reconcile.judge(
                ac_no,
                rows,
                published.get(ac_no, 0),
                totals,
                male.get(ac_no, 0),
                female.get(ac_no, 0),
                boxes or None,
            )
        )

    if not verdicts:
        print(f"no shards under {args.shards}", file=sys.stderr)
        return 1
    print(reconcile.render(verdicts))
    return 0 if all(v.ok for v in verdicts) else 1


def cmd_verify(args: argparse.Namespace) -> int:
    """Structure first, then pixels. No API calls."""
    from . import verify

    stage_dir = Path(args.stage1_dir)
    parts = sorted(stage_dir.glob("part*"))
    if not parts:
        print(f"no parts under {stage_dir}", file=sys.stderr)
        return 1

    by_part = {}
    if args.zip:
        zip_path = Path(args.zip)
        by_part = {p.part_no: p.pdf_name for p in render.iter_zip_parts(zip_path)}

    checks = []
    for part_dir in parts:
        check = verify.check_structure(part_dir)
        if args.zip and check.ok and check.part_no in by_part:
            verify.check_pixels(
                Path(args.zip),
                by_part[check.part_no],
                part_dir,
                check,
                sample=None if args.all or args.sample == 0 else args.sample,
            )
        checks.append(check)
        mark = "ok" if check.ok else f"{len(check.problems)} PROBLEM(S)"
        print(
            f"  part {check.part_no:<4} {check.tiles:>5} tiles  "
            f"{check.identical}/{check.compared} identical  {mark}"
        )

    print()
    print(verify.render_report(checks))
    return 0 if all(c.ok for c in checks) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="electors", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="extract electors from one AC zip")
    p_parse.add_argument("zip")
    p_parse.add_argument("--out", default=str(output.SHARD_DIR))
    p_parse.add_argument("--workers", type=int, default=8)
    p_parse.add_argument("--limit", type=int, default=0, help="first N parts only")
    p_parse.add_argument("--cache", default="out/electors/cache")
    p_parse.add_argument(
        "--engine",
        default="tesseract",
        choices=("tesseract", "vision"),
        help="vision reads 30x better and costs about $1.70 an AC; tesseract is free and cannot"
        " read a name",
    )
    p_parse.add_argument("--api-key", default="", help="Cloud Vision key, or set VISION_API_KEY")
    p_parse.add_argument(
        "--capture",
        action="store_true",
        help="also cache the OCR text, so later parsing fixes score without re-reading",
    )
    p_parse.set_defaults(func=cmd_parse)

    p_quality = sub.add_parser("quality", help="measure field quality on a sample of parts")
    p_quality.add_argument("zip")
    p_quality.add_argument("--parts", type=int, default=20)
    p_quality.add_argument("--seed", type=int, default=7)
    p_quality.add_argument("--workers", type=int, default=6)
    p_quality.add_argument("--cache", default="out/electors/cache")
    p_quality.add_argument("--out", type=Path, default=Path("out/electors/quality.json"))
    p_quality.set_defaults(func=cmd_quality)

    p_diag = sub.add_parser("diagnose", help="where failures concentrate and what to try")
    p_diag.add_argument("--cache", default="out/electors/cache")
    p_diag.add_argument("--ac", type=int, default=1)
    p_diag.set_defaults(func=cmd_diagnose)

    p_bench = sub.add_parser("bench", help="record a baseline, or gate a fix against one")
    p_bench.add_argument("--record", action="store_true")
    p_bench.add_argument("--target", default="epic_present")
    p_bench.add_argument("--seconds", type=float, default=0.0, help="measured seconds per part")
    p_bench.add_argument("--cache", default="out/electors/cache")
    p_bench.add_argument("--ac", type=int, default=1)
    p_bench.add_argument(
        "--against",
        help="git revision to compare against, replaying the line cache (e.g. HEAD~1)",
    )
    p_bench.add_argument("--parts", type=int, nargs="*")
    p_bench.add_argument(
        "--higher-is-better",
        action="store_true",
        help="the target improves by rising (default: it is an error rate and improves by falling)",
    )
    p_bench.set_defaults(func=cmd_bench)

    p_capture = sub.add_parser("capture", help="cache the OCR text so parsing fixes score fast")
    p_capture.add_argument("zip")
    p_capture.add_argument("--parts", type=int, nargs="*", help="part numbers (default: all)")
    p_capture.add_argument("--cache", default="out/cache/electors")
    p_capture.add_argument("--workers", type=int, default=4)
    p_capture.set_defaults(func=cmd_capture)

    p_replay = sub.add_parser("replay", help="re-parse cached OCR text with today's code")
    p_replay.add_argument("--parts", type=int, nargs="*")
    p_replay.add_argument("--ac", type=int, default=1)
    p_replay.add_argument("--diagnose", action="store_true")
    p_replay.set_defaults(func=cmd_replay)

    p_esc = sub.add_parser("escalate", help="which rows want a second, more expensive read")
    p_esc.add_argument("--cache", default="out/cache/electors")
    p_esc.add_argument("--ac", type=int, default=1)
    p_esc.add_argument("--parts", type=int, nargs="*")
    p_esc.add_argument("--replay", action="store_true", help="source rows from the line cache")
    p_esc.set_defaults(func=cmd_escalate)

    p_rebuild = sub.add_parser(
        "rebuild",
        help="rewrite the shard from cached OCR text, with today's parser and no OCR",
    )
    p_rebuild.add_argument("--parts", type=int, nargs="*")
    p_rebuild.add_argument("--ac", type=int, default=1)
    p_rebuild.add_argument("--out", type=Path, default=Path("out/electors"))
    p_rebuild.add_argument("--cache", default="out/electors/cache")
    p_rebuild.set_defaults(func=cmd_rebuild)

    p_second = sub.add_parser(
        "second-pass",
        help="re-read boxes the cheap pass could not, with savitr's roll model",
    )
    p_second.add_argument("zip")
    p_second.add_argument("--parts", type=int, nargs="+", required=True)
    p_second.set_defaults(func=cmd_second_pass)

    p_res = sub.add_parser(
        "resolution",
        help="what resolution to feed Vision: same parts at 400 dpi, 300 dpi and native",
    )
    p_res.add_argument("zip")
    p_res.add_argument("--api-key", default="", help="or set VISION_API_KEY")
    p_res.add_argument("--limit", type=int, default=10, help="first N parts (agreement arm)")
    p_res.add_argument("--truth", default="dataset/eval", help="directory holding truth.json")
    p_res.add_argument(
        "--truth-parts",
        action="store_true",
        help="run only the parts the hand-read boxes come from, which is six pages not ten parts",
    )
    p_res.add_argument("--arms", default="", help="comma-separated subset of the arms")
    p_res.add_argument("--save", default="", help="write the readings so analysis can be redone")
    p_res.add_argument("--load", default="", help="re-report saved readings, spending nothing")
    p_res.add_argument(
        "--dots",
        default="",
        help="dots_readings.json from the Kaggle notebook, compared as another arm",
    )
    p_res.add_argument(
        "--stack",
        type=int,
        default=0,
        help="force pages-per-image for every arm, separating resolution from stack depth",
    )
    p_res.set_defaults(func=cmd_resolution)

    p_crops = sub.add_parser(
        "crops",
        help="cut box images out for a second reader running on another machine",
    )
    p_crops.add_argument("zip")
    p_crops.add_argument("--out", default="out/crops", help="directory to write the PNGs into")
    p_crops.add_argument("--parts", type=int, default=8, help="first N parts of the zip")
    p_crops.add_argument("--limit", type=int, default=10_000, help="stop after this many crops")
    p_crops.add_argument("--dpi", type=int, default=extract.DPI)
    p_crops.add_argument("--zip-to", default="", help="also zip the folder to this path")
    p_crops.set_defaults(func=cmd_crops)

    p_s1 = sub.add_parser(
        "stage1", help="render, find boxes, repack for Vision -- CPU only, no API calls"
    )
    p_s1.add_argument("zip")
    p_s1.add_argument("--out", default="out/stage1")
    p_s1.add_argument("--workers", type=int, default=8)
    p_s1.add_argument("--limit", type=int, default=0, help="first N parts only")
    p_s1.add_argument("--dpi", type=int, default=extract.DPI)
    p_s1.set_defaults(func=cmd_stage1)

    p_run = sub.add_parser("run", help="zip -> stage 1 -> Vision -> Parquet shard, per AC")
    p_run.add_argument("zips", nargs="+", help="one or more AC zips, e.g. data/ac_rolls/AC*.zip")
    p_run.add_argument("--work", default="out/stage1", help="where prepared parts live")
    p_run.add_argument("--shards", default=str(output.SHARD_DIR))
    p_run.add_argument("--manifest", default=str(output.MANIFEST))
    p_run.add_argument("--workers", type=int, default=8)
    p_run.add_argument("--limit", type=int, default=0, help="first N parts of each AC only")
    p_run.add_argument(
        "--prepare-only", action="store_true", help="stage one only -- spend no money"
    )
    p_run.set_defaults(func=cmd_run)

    p_pull = sub.add_parser("pull", help="bring finished constituencies home and re-judge them")
    p_pull.add_argument("--bucket", default="gs://sawasdee-assam-rolls")
    p_pull.add_argument("--home", default="data/electors")
    p_pull.add_argument("--words", default="data/words")
    p_pull.add_argument("--no-words", action="store_true", help="shards only, no Vision cache")
    p_pull.add_argument("--watch", action="store_true", help="keep polling as constituencies land")
    p_pull.add_argument("--every", type=int, default=300)
    p_pull.set_defaults(func=cmd_pull)

    p_floor = sub.add_parser(
        "floor", help="how many rows are provably wrong, counted without reading a page"
    )
    p_floor.add_argument("--home", default="data/electors")
    p_floor.add_argument("--ac", type=int, help="one constituency, rather than all of them")
    p_floor.set_defaults(func=cmd_floor)

    p_fleet = sub.add_parser("fleet", help="where the run is: done, in flight, queued, stalled")
    p_fleet.add_argument("--bucket", default="gs://sawasdee-assam-rolls")
    p_fleet.set_defaults(func=cmd_fleet)

    p_bundle = sub.add_parser(
        "bundle", help="backfill verification bundles for constituencies that finished without one"
    )
    p_bundle.add_argument("--bucket", default="gs://sawasdee-assam-rolls")
    p_bundle.add_argument("--home", default="data/electors")
    p_bundle.set_defaults(func=cmd_bundle)

    p_reap = sub.add_parser(
        "reap", help="delete source archives whose constituency is home, verified and passing"
    )
    p_reap.add_argument("--bucket", default="gs://sawasdee-assam-rolls")
    p_reap.add_argument("--home", default="data/electors")
    p_reap.add_argument("--yes", action="store_true", help="actually delete; otherwise a dry run")
    p_reap.set_defaults(func=cmd_reap)

    p_status = sub.add_parser("status", help="one verdict per constituency, with its evidence")
    p_status.add_argument("--shards", default=str(output.SHARD_DIR))
    p_status.add_argument("--work", default="out/stage1")
    p_status.add_argument("--info", default="dataset/parts.jsonl.gz")
    p_status.set_defaults(func=cmd_status)

    p_ver = sub.add_parser(
        "verify", help="prove the repack lost nothing and placed every tile correctly"
    )
    p_ver.add_argument("stage1_dir", help="e.g. out/stage1/AC001")
    p_ver.add_argument("--zip", default="", help="source zip, needed for the pixel check")
    p_ver.add_argument("--sample", type=int, default=40, help="tiles per part; 0 means all")
    p_ver.add_argument("--all", action="store_true", help="compare every tile, not a sample")
    p_ver.set_defaults(func=cmd_verify)

    p_report = sub.add_parser("report", help="reconcile a shard against the info pages")
    p_report.add_argument("shard")
    p_report.set_defaults(func=cmd_report)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
