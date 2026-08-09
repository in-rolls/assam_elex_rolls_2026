"""Command line for the elector stage.

    python -m electors parse data/ac_rolls/AC1_ASM.zip     extract one AC
    python -m electors report out/electors/AC001.parquet   reconcile against the info pages

Parts are independent, so they run in a process pool. The unit of retry is the part too: a
part that fails leaves the rest of the AC intact and is listed rather than silently dropped.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
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
    zip_path, pdf_name, cache_dir, *rest = args
    zip_path, cache_dir = Path(zip_path), Path(cache_dir)
    capture_lines = bool(rest[0]) if rest else False
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

    ac_no = parts[0].ac_no
    cache_dir = Path(args.cache) / f"AC{ac_no:03d}"
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
    clock = timing.RunClock(total=len(parts))

    rows: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        payload = [(str(zip_path), p.pdf_name, str(cache_dir), args.capture) for p in parts]
        for result in pool.map(_one_part, payload):
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
        },
    )

    for line in clock.summary():
        log.info("%s", line)
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
        payload = [(str(zip_path), p.pdf_name, str(cache_dir)) for p in chosen]
        for done, result in enumerate(pool.map(_one_part, payload), start=1):
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
            f"no cached parts under {args.cache}; run `quality` or `parse` first", file=sys.stderr
        )
        return 1

    print(f"{len(rows):,} rows from cache\n")
    print(diagnose.render_priorities(diagnose.priorities(rows)))
    found = diagnose.associations(rows)
    print()
    print(diagnose.render(found, diagnose.proposals(found)))
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    """Record a baseline, or measure the current code against one."""
    from . import bench, quality

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
        payload = [(str(zip_path), p.pdf_name, str(cache_dir), True) for p in chosen]
        for done, result in enumerate(pool.map(_one_part, payload), start=1):
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


def cmd_report(args: argparse.Namespace) -> int:
    rows = output.read_shard(Path(args.shard))
    checks = validate.reconcile(rows, validate.load_part_totals())
    summary = validate.summarize(checks, rows)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


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

    p_report = sub.add_parser("report", help="reconcile a shard against the info pages")
    p_report.add_argument("shard")
    p_report.set_defaults(func=cmd_report)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
