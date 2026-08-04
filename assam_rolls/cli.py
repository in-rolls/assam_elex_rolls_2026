"""Command line interface.

Pipeline stages, each independently resumable:

    render   zips            -> out/pages/{ac}-{part}.png
    extract  pages           -> Batch API jobs (or --sync for a small pilot)
    collect  batch jobs      -> out/raw/{ac}-{part}.json
    build    raw JSON        -> out/parts.csv, out/part_sections.csv, out/report.json
    review   parts + pages   -> out/review.html
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import extract as extract_mod
from . import log as _log
from . import render as render_mod
from . import review as review_mod
from . import validate as validate_mod
from .schema import PART_COLUMNS, SECTION_COLUMNS


def _zip_paths(zip_dir: Path) -> List[Path]:
    return sorted(zip_dir.glob("*.zip"))


def _all_refs(zip_dir: Path) -> List[render_mod.PartRef]:
    refs: List[render_mod.PartRef] = []
    for zip_path in _zip_paths(zip_dir):
        refs.extend(render_mod.iter_zip_parts(zip_path))
    return refs


def _parts_per_ac(zip_dir: Path) -> Dict[int, int]:
    counts: Dict[int, int] = {}
    for ref in _all_refs(zip_dir):
        counts[ref.ac_no] = counts.get(ref.ac_no, 0) + 1
    return counts


def _write_csv(path: Path, columns: List[str], rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _client():
    import anthropic

    return anthropic.Anthropic()


# ---------------------------------------------------------------------------- commands


def cmd_render(args: argparse.Namespace) -> int:
    render_mod.require_poppler()
    zip_paths = _zip_paths(args.zip_dir)
    if not zip_paths:
        print(f"no zips found in {args.zip_dir}", file=sys.stderr)
        return 1

    total = 0
    for zip_path in zip_paths:
        unknown = render_mod.unrecognized_pdfs(zip_path)
        if unknown:
            print(f"  warning: {len(unknown)} unrecognized PDF(s) in {zip_path.name}")
        refs = render_mod.render_zip(
            zip_path, args.out, page=args.page, limit=args.limit, overwrite=args.overwrite
        )
        total += len(refs)
        print(f"  {zip_path.name}: {len(refs)} parts -> {args.out}")
    print(f"rendered {total} pages")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    refs = _all_refs(args.zip_dir)
    requests = extract_mod.load_pages(args.pages, refs)
    if args.limit:
        requests = requests[: args.limit]
    if not requests:
        print(f"no rendered pages found in {args.pages}; run render first", file=sys.stderr)
        return 1

    args.raw.mkdir(parents=True, exist_ok=True)
    pending = [r for r in requests if not (args.raw / f"{r.key}.json").exists()]
    print(f"{len(requests)} pages, {len(pending)} still to extract")
    if not pending:
        return 0

    client = _client()

    if args.sync:
        for request in pending:
            try:
                parsed = extract_mod.extract_page(
                    client, request.image_bytes, model=args.model, effort=args.effort
                )
            except extract_mod.ExtractionError as exc:
                parsed = {"_error": str(exc)}
                print(f"  {request.key}: {exc}", file=sys.stderr)
            (args.raw / f"{request.key}.json").write_text(
                json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"  {request.key}: done")
        return 0

    batch_ids = extract_mod.submit_batches(client, pending, model=args.model, effort=args.effort)
    manifest = args.raw.parent / "batches.json"
    existing = json.loads(manifest.read_text()) if manifest.exists() else []
    manifest.write_text(json.dumps(sorted(set(existing) | set(batch_ids)), indent=2))
    print(f"submitted {len(batch_ids)} batch(es); ids saved to {manifest}")
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    manifest = args.raw.parent / "batches.json"
    if not manifest.exists():
        print(f"no batch manifest at {manifest}", file=sys.stderr)
        return 1

    client = _client()
    batch_ids = json.loads(manifest.read_text())
    args.raw.mkdir(parents=True, exist_ok=True)

    for batch_id in batch_ids:
        while not extract_mod.batch_is_done(client, batch_id):
            if not args.wait:
                print(f"  {batch_id}: still processing (use --wait to block)")
                break
            print(f"  {batch_id}: processing, sleeping {args.poll_seconds}s")
            time.sleep(args.poll_seconds)
        else:
            results = extract_mod.collect_batch(client, batch_id)
            for key, parsed in results.items():
                (args.raw / f"{key}.json").write_text(
                    json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            print(f"  {batch_id}: wrote {len(results)} results")
    return 0


def cmd_build_from_model(args: argparse.Namespace) -> int:
    refs = {ref.key: ref for ref in _all_refs(args.zip_dir)}
    raw_files = sorted(args.raw.glob("*.json"))
    if not raw_files:
        print(f"no extraction output in {args.raw}", file=sys.stderr)
        return 1

    part_rows: List[Dict[str, Any]] = []
    section_rows: List[Dict[str, Any]] = []

    for raw_file in raw_files:
        key = raw_file.stem
        ref = refs.get(key)
        if ref is None:
            print(f"  warning: {key} has no matching source PDF; skipped")
            continue
        parsed = json.loads(raw_file.read_text(encoding="utf-8"))
        image_path = args.pages / f"{key}.png"
        part_rows.append(
            extract_mod.to_part_row(
                ref,
                parsed,
                image_bytes=image_path.read_bytes() if image_path.exists() else None,
                model=args.model,
            )
        )
        section_rows.extend(extract_mod.to_section_rows(ref, parsed))

    validate_mod.validate_rows(part_rows, parts_per_ac=_parts_per_ac(args.zip_dir))
    report = validate_mod.accuracy_report(part_rows)

    _write_csv(args.out / "parts.csv", PART_COLUMNS, part_rows)
    _write_csv(args.out / "part_sections.csv", SECTION_COLUMNS, section_rows)
    (args.out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"parts.csv         {len(part_rows)} rows")
    print(f"part_sections.csv {len(section_rows)} rows")
    print("\nquality (filename cross-check is true ground truth on every page):")
    for key, value in report.items():
        print(f"  {key:24s} {value}")
    return 0


def _ocr_one(job):
    """Worker: read one page into rows and cache the result. Top-level so it pickles."""
    from PIL import Image

    from . import cache as cache_mod
    from . import layout as layout_mod
    from . import ocr as ocr_mod
    from . import parse as parse_mod
    from . import render as render_mod
    from .log import get_logger

    ref, image_path, engine_name, zip_path, cache_dir = job
    worker_log = get_logger("worker")

    try:
        pdf_bytes = render_mod.read_pdf_bytes(zip_path, ref.pdf_name)
        pdf_sha = render_mod.sha256_bytes(pdf_bytes)
    except Exception as exc:  # a missing or corrupt zip entry
        return ref.key, None, f"source unreadable: {exc}"

    cached = cache_mod.read_entry(cache_dir, ref.key)
    if cache_mod.is_fresh(cached, pdf_sha):
        return ref.key, "cached", None

    page_bytes = Path(image_path).read_bytes()
    provenance = {
        "source_zip_dir": str(Path(zip_path).parent),
        "pdf_sha256": pdf_sha,
        "pdf_bytes": len(pdf_bytes),
        "page_png": str(image_path),
        "page_sha256": render_mod.sha256_bytes(page_bytes),
        "engine_version": ocr_mod.engine_version(engine_name),
    }

    image = Image.open(image_path)
    try:
        grid = layout_mod.build_grid(image)
    except layout_mod.LayoutError as exc:
        worker_log.warning("%s: layout failed: %s", ref.key, exc)
        row = parse_mod.failed_row(ref, engine_name, str(exc), provenance)
        cache_mod.write_entry(cache_dir, ref.key, row, [], pdf_sha)
        return ref.key, "done", None

    try:
        engine = ocr_mod.get_engine(engine_name)
        row, sections = parse_mod.parse_page(image, grid, ref, engine, provenance)
    except Exception as exc:
        return ref.key, None, f"{type(exc).__name__}: {exc}"

    cache_mod.write_entry(cache_dir, ref.key, row, sections, pdf_sha)
    return ref.key, "done", None


def cmd_ocr(args: argparse.Namespace) -> int:
    """Local OCR over rendered pages -- the free path, no API calls. Resumable."""
    import multiprocessing as mp

    from . import cache as cache_mod
    from .log import RunSummary

    logger = _log.get_logger(__name__)

    zip_for_ac = {}
    for zip_path in _zip_paths(args.zip_dir):
        for ref in render_mod.iter_zip_parts(zip_path):
            zip_for_ac[ref.key] = zip_path

    refs = [r for r in _all_refs(args.zip_dir) if (args.pages / f"{r.key}.png").exists()]
    if args.limit:
        refs = refs[: args.limit]
    if not refs:
        logger.error("no rendered pages in %s; run render first", args.pages)
        return 1

    cache_dir = args.cache
    if args.overwrite:
        cache_mod.clear(cache_dir)

    jobs = [
        (ref, args.pages / f"{ref.key}.png", args.engine, zip_for_ac[ref.key], cache_dir)
        for ref in refs
        if ref.key in zip_for_ac
    ]
    workers = args.workers or max(1, (os.cpu_count() or 2) - 1)
    logger.info(
        "ocr: %d pages, engine=%s, %d workers, cache=%s", len(jobs), args.engine, workers, cache_dir
    )
    print(f"{len(jobs)} pages, engine={args.engine}, {workers} workers")

    summary = RunSummary(logger, "ocr")
    log_file = str(args.log_file) if args.log_file else None

    def record(result):
        key, status, error = result
        if error:
            summary.failed(key, error)
        elif status == "cached":
            summary.was_cached(key)
        else:
            summary.succeeded(key)

    if workers == 1:
        for job in jobs:
            record(_ocr_one(job))
    else:
        with mp.Pool(
            workers, initializer=_log.worker_init, initargs=(log_file, logging.WARNING)
        ) as pool:
            for result in pool.imap_unordered(_ocr_one, jobs, chunksize=4):
                record(result)

    stats = summary.emit()
    print(
        f"\n{stats['processed']} extracted, {stats['cached']} already cached, "
        f"{stats['failed']} failed in {stats['elapsed_seconds']:.0f}s"
    )
    if stats["failed"]:
        print(f"see {args.log_file} for the failing part keys")
    print(f"\nnow run: assam-rolls build --cache {cache_dir} --out {args.out}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    """Assemble the dataset from the cache: validate, canonicalise, write JSONL + CSV."""
    from . import cache as cache_mod
    from . import dictionary as dict_mod
    from . import output as output_mod

    logger = _log.get_logger(__name__)
    entries = sorted(args.cache.glob("*.json"))
    if not entries:
        logger.error("no cache entries in %s; run ocr first", args.cache)
        return 1

    part_rows, section_rows = [], []
    for path in entries:
        entry = cache_mod.read_entry(args.cache, path.stem)
        if not entry:
            continue
        part_rows.append(entry["row"])
        section_rows.extend(entry.get("sections") or [])
    logger.info("build: loaded %d parts, %d sections", len(part_rows), len(section_rows))

    validate_mod.validate_rows(part_rows, parts_per_ac=_parts_per_ac(args.zip_dir))
    report = validate_mod.accuracy_report(part_rows)

    # Canonical values are written alongside the raw ones, never over them.
    corpus = dict_mod.build(part_rows)
    part_rows = [corpus.apply_row(row) for row in part_rows]
    report["dictionary_substitutions"] = len(corpus.changes())
    report["dictionary_contested"] = len(corpus.contested())

    counts = output_mod.write_dataset(args.out, part_rows, section_rows)
    (args.out / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    for name, n in counts.items():
        print(f"{name:20s} {n} rows")
    print("\nquality (filename and arithmetic checks are ground truth on every page):")
    for key, value in report.items():
        print(f"  {key:26s} {value}")
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    """Score a candidate engine against a declared reference engine."""
    from PIL import Image

    from . import bench as bench_mod
    from . import layout as layout_mod
    from . import ocr as ocr_mod
    from . import parse as parse_mod

    refs = [r for r in _all_refs(args.zip_dir) if (args.pages / f"{r.key}.png").exists()]
    by_ac: Dict[int, List[Any]] = {}
    for ref in refs:
        by_ac.setdefault(ref.ac_no, []).append(ref)
    # Stratify: an equal slice of each AC, so one constituency cannot dominate.
    per_ac = max(1, args.pages_per_ac)
    sample = [r for ac in sorted(by_ac) for r in by_ac[ac][::7][:per_ac]]
    print(f"benchmark on {len(sample)} pages across {len(by_ac)} ACs")

    results: Dict[str, Dict[Any, Dict[str, Any]]] = {}
    timings: Dict[str, float] = {}
    for name in (args.reference, args.candidate):
        engine = ocr_mod.get_engine(name)
        rows: Dict[Any, Dict[str, Any]] = {}
        started = time.time()
        for i, ref in enumerate(sample, 1):
            image = Image.open(args.pages / f"{ref.key}.png")
            try:
                grid = layout_mod.build_grid(image)
                row, _ = parse_mod.parse_page(image, grid, ref, engine)
                rows[(ref.ac_no, ref.part_no)] = row
            except (layout_mod.LayoutError, ocr_mod.OCRError) as exc:
                print(f"  {name} {ref.key}: {exc}", file=sys.stderr)
            print(f"  {name}: {i}/{len(sample)}", end="\r", flush=True)
        timings[name] = (time.time() - started) / max(1, len(sample))
        results[name] = rows
        if hasattr(engine, "close"):
            engine.close()
        print(f"  {name}: {len(rows)} pages, {timings[name]:.1f}s/page")

    score = bench_mod.score_engine(
        results[args.reference],
        results[args.candidate],
        args.candidate,
        seconds_per_page=timings[args.candidate],
    )
    text = bench_mod.render_report(
        [score],
        reference_name=args.reference,
        n_pages=len(results[args.reference]),
        notes=[
            f"reference `{args.reference}` ran at {timings[args.reference]:.1f}s/page, "
            f"candidate `{args.candidate}` at {timings[args.candidate]:.1f}s/page",
            "numeric fields are excluded: both engines read them with the same digit "
            "pass, and they are already verified by the checks in validate.py",
        ],
    )
    bench_mod.write_report(args.out, text, [score])
    print(f"\nwrote {args.out}\n")
    print(text)
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    rows = list(csv.DictReader(args.parts.open(encoding="utf-8")))
    html = review_mod.build_review_html(rows, args.pages, only_flagged=not args.all)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


# ------------------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="assam-rolls", description=__doc__)
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="defaults to out/logs/run-<timestamp>.log",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug output on console")
    parser.add_argument("-q", "--quiet", action="store_true", help="errors only on console")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("--zip-dir", type=Path, default=Path("data/ac_info"))
        p.add_argument("--pages", type=Path, default=Path("out/pages"))

    p_render = sub.add_parser("render", help="zips -> page PNGs")
    p_render.add_argument("--zip-dir", type=Path, default=Path("data/ac_info"))
    p_render.add_argument("--out", type=Path, default=Path("out/pages"))
    p_render.add_argument("--page", type=int, default=render_mod.PAGE_FORM)
    p_render.add_argument("--limit", type=int, default=None, help="per zip")
    p_render.add_argument("--overwrite", action="store_true")
    p_render.set_defaults(func=cmd_render)

    p_extract = sub.add_parser("extract", help="page PNGs -> Claude")
    add_common(p_extract)
    p_extract.add_argument("--raw", type=Path, default=Path("out/raw"))
    p_extract.add_argument("--model", default=extract_mod.DEFAULT_MODEL)
    p_extract.add_argument("--effort", default=extract_mod.DEFAULT_EFFORT)
    p_extract.add_argument("--limit", type=int, default=None)
    p_extract.add_argument("--sync", action="store_true", help="synchronous, for small pilots")
    p_extract.set_defaults(func=cmd_extract)

    p_ocr = sub.add_parser("ocr", help="local OCR -> per-part cache (free, resumable)")
    add_common(p_ocr)
    p_ocr.add_argument("--out", type=Path, default=Path("out"))
    p_ocr.add_argument("--cache", type=Path, default=Path("out/cache"))
    p_ocr.add_argument("--engine", default="tesseract")
    p_ocr.add_argument("--limit", type=int, default=None)
    p_ocr.add_argument("--workers", type=int, default=None)
    p_ocr.add_argument(
        "--overwrite", action="store_true", help="discard the cache and re-extract everything"
    )
    p_ocr.set_defaults(func=cmd_ocr)

    p_build = sub.add_parser("build", help="cache -> parts.jsonl + CSVs (validated)")
    add_common(p_build)
    p_build.add_argument("--cache", type=Path, default=Path("out/cache"))
    p_build.add_argument("--out", type=Path, default=Path("out"))
    p_build.set_defaults(func=cmd_build)

    p_collect = sub.add_parser("collect", help="fetch finished batches")
    p_collect.add_argument("--raw", type=Path, default=Path("out/raw"))
    p_collect.add_argument("--wait", action="store_true")
    p_collect.add_argument("--poll-seconds", type=int, default=60)
    p_collect.set_defaults(func=cmd_collect)

    p_model_build = sub.add_parser("build-model", help="Claude raw JSON -> CSVs (dormant path)")
    add_common(p_model_build)
    p_model_build.add_argument("--raw", type=Path, default=Path("out/raw"))
    p_model_build.add_argument("--out", type=Path, default=Path("out"))
    p_model_build.add_argument("--model", default=extract_mod.DEFAULT_MODEL)
    p_model_build.set_defaults(func=cmd_build_from_model)

    p_bench = sub.add_parser("bench", help="score one engine against a declared reference")
    add_common(p_bench)
    p_bench.add_argument("--reference", default="surya")
    p_bench.add_argument("--candidate", default="tesseract")
    p_bench.add_argument("--pages-per-ac", type=int, default=4)
    p_bench.add_argument("--out", type=Path, default=Path("out/REPORT.md"))
    p_bench.set_defaults(func=cmd_bench)

    p_review = sub.add_parser("review", help="HTML review sheet")
    p_review.add_argument("--parts", type=Path, default=Path("out/parts.csv"))
    p_review.add_argument("--pages", type=Path, default=Path("out/pages"))
    p_review.add_argument("--out", type=Path, default=Path("out/review.html"))
    p_review.add_argument("--all", action="store_true", help="include clean rows too")
    p_review.set_defaults(func=cmd_review)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # A statewide run is hours long; without a file log a failure leaves no record of
    # which page failed or why.
    out_dir = getattr(args, "out", None)
    if not isinstance(out_dir, Path):
        out_dir = Path("out")
    elif out_dir.suffix:
        # `review --out out/review.html` names a file, not a directory; putting the log
        # under it would create a directory where the HTML belongs.
        out_dir = out_dir.parent
    if args.log_file is None:
        args.log_file = _log.default_log_path(out_dir)
    level = logging.DEBUG if args.verbose else (logging.ERROR if args.quiet else logging.INFO)
    _log.setup_logging(level=level, log_file=args.log_file)
    logger = _log.get_logger(__name__)
    logger.info("assam-rolls %s: %s", args.command, " ".join(argv or sys.argv[1:]))
    logger.debug("log file: %s", args.log_file)

    try:
        return args.func(args)
    except KeyboardInterrupt:
        # The cache makes this recoverable; say so rather than dumping a traceback.
        logger.warning("interrupted; progress is cached, re-run to resume")
        print("\ninterrupted — progress is cached, re-run the same command to resume")
        return 130
    except Exception:
        logger.exception("unhandled error in %s", args.command)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
