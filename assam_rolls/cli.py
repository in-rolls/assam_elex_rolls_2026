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
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import extract as extract_mod
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


def cmd_build(args: argparse.Namespace) -> int:
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
    """Worker: read one rendered page into rows. Top-level so it can be pickled."""
    from PIL import Image

    from . import layout as layout_mod
    from . import ocr as ocr_mod
    from . import parse as parse_mod

    ref, image_path, engine_name = job
    engine = ocr_mod.get_engine(engine_name)
    image = Image.open(image_path)
    try:
        grid = layout_mod.build_grid(image)
    except layout_mod.LayoutError as exc:
        row = {
            "source_zip": ref.zip_name,
            "source_pdf": ref.pdf_name,
            "ac_no_file": ref.ac_no,
            "part_no_file": ref.part_no,
            "model": engine_name,
            "template_match": False,
            "flags": "layout_failed",
            "needs_review": True,
            "anomaly_notes": str(exc)[:200],
        }
        return row, []
    return parse_mod.parse_page(image, grid, ref, engine)


def cmd_ocr(args: argparse.Namespace) -> int:
    """Local OCR over rendered pages -- the free path, no API calls."""
    import multiprocessing as mp
    from datetime import datetime, timezone

    refs = [r for r in _all_refs(args.zip_dir) if (args.pages / f"{r.key}.png").exists()]
    if args.limit:
        refs = refs[: args.limit]
    if not refs:
        print(f"no rendered pages in {args.pages}; run render first", file=sys.stderr)
        return 1

    jobs = [(ref, args.pages / f"{ref.key}.png", args.engine) for ref in refs]
    workers = args.workers or max(1, (os.cpu_count() or 2) - 1)
    print(f"{len(jobs)} pages, engine={args.engine}, {workers} workers")

    started = time.time()
    if workers == 1:
        results = [_ocr_one(job) for job in jobs]
    else:
        with mp.Pool(workers) as pool:
            results = pool.map(_ocr_one, jobs, chunksize=4)
    elapsed = time.time() - started

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    part_rows, section_rows = [], []
    for row, sections in results:
        row.setdefault("extracted_at", stamp)
        part_rows.append(row)
        section_rows.extend(sections)

    validate_mod.validate_rows(part_rows, parts_per_ac=_parts_per_ac(args.zip_dir))
    report = validate_mod.accuracy_report(part_rows)
    report["engine"] = args.engine
    report["seconds_per_page"] = round(elapsed / max(1, len(jobs)), 3)

    _write_csv(args.out / "parts.csv", PART_COLUMNS, part_rows)
    _write_csv(args.out / "part_sections.csv", SECTION_COLUMNS, section_rows)
    (args.out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"\nparts.csv         {len(part_rows)} rows")
    print(f"part_sections.csv {len(section_rows)} rows")
    per_page = elapsed / max(1, len(jobs))
    print(f"elapsed           {elapsed:.0f}s ({per_page:.2f}s/page)")
    print("\nquality (filename and arithmetic checks are ground truth on every page):")
    for key, value in report.items():
        print(f"  {key:24s} {value}")
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

    p_ocr = sub.add_parser("ocr", help="local OCR -> validated CSVs (free, no API)")
    add_common(p_ocr)
    p_ocr.add_argument("--out", type=Path, default=Path("out"))
    p_ocr.add_argument("--engine", default="tesseract")
    p_ocr.add_argument("--limit", type=int, default=None)
    p_ocr.add_argument("--workers", type=int, default=None)
    p_ocr.set_defaults(func=cmd_ocr)

    p_collect = sub.add_parser("collect", help="fetch finished batches")
    p_collect.add_argument("--raw", type=Path, default=Path("out/raw"))
    p_collect.add_argument("--wait", action="store_true")
    p_collect.add_argument("--poll-seconds", type=int, default=60)
    p_collect.set_defaults(func=cmd_collect)

    p_build = sub.add_parser("build", help="raw JSON -> validated CSVs")
    add_common(p_build)
    p_build.add_argument("--raw", type=Path, default=Path("out/raw"))
    p_build.add_argument("--out", type=Path, default=Path("out"))
    p_build.add_argument("--model", default=extract_mod.DEFAULT_MODEL)
    p_build.set_defaults(func=cmd_build)

    p_review = sub.add_parser("review", help="HTML review sheet")
    p_review.add_argument("--parts", type=Path, default=Path("out/parts.csv"))
    p_review.add_argument("--pages", type=Path, default=Path("out/pages"))
    p_review.add_argument("--out", type=Path, default=Path("out/review.html"))
    p_review.add_argument("--all", action="store_true", help="include clean rows too")
    p_review.set_defaults(func=cmd_review)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
