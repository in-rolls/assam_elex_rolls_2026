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
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from assam_rolls import ocr, render

from . import extract, output, validate


def _one_part(args) -> Dict[str, Any]:
    """Worker entry point. Top-level so it pickles; the engine is built per process."""
    zip_path, pdf_name = args
    engine = ocr.get_engine("tesseract", lang="asm")
    result = extract.read_part(Path(zip_path), pdf_name, engine=engine)
    return {
        "ac_no": result.ac_no,
        "part_no": result.part_no,
        "electors": result.electors,
        "page_count": result.page_count,
        "elector_pages": result.elector_pages,
        "unknown_pages": result.unknown_pages,
        "error": result.error,
    }


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
    print(f"{zip_path.name}: {len(parts)} parts, AC {ac_no}, {args.workers} workers")

    rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        payload = [(str(zip_path), p.pdf_name) for p in parts]
        for result in pool.map(_one_part, payload):
            done += 1
            if result["error"] or result["unknown_pages"]:
                failures.append(result)
            rows.extend(result["electors"])
            if done % 10 == 0 or done == len(parts):
                print(
                    f"  {done}/{len(parts)} parts | {len(rows):,} electors | "
                    f"{len(failures)} with problems"
                )

    if not rows:
        print("no electors extracted", file=sys.stderr)
        return 1

    totals = validate.load_part_totals()
    checks = validate.reconcile(rows, totals)
    summary = validate.summarize(checks, rows)

    path = output.write_shard(rows, ac_no, Path(args.out))
    entry = output.update_manifest(
        ac_no,
        path,
        {
            "rows": summary["electors_extracted"],
            "expected": summary["electors_expected"],
            "parts_exact_rate": round(summary["parts_exact_rate"], 4),
            "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )

    print(f"\nwrote {path} ({entry['bytes'] / 1e6:.1f} MB)")
    print(
        f"count reconciliation: {summary['parts_exact']}/{summary['parts_with_published_total']} "
        f"parts exact ({summary['parts_exact_rate']:.1%})"
    )
    print(
        f"electors: {summary['electors_extracted']:,} extracted vs "
        f"{summary['electors_expected']:,} published"
    )
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
    p_parse.set_defaults(func=cmd_parse)

    p_report = sub.add_parser("report", help="reconcile a shard against the info pages")
    p_report.add_argument("shard")
    p_report.set_defaults(func=cmd_report)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
