"""Build and verify the statewide elector release from its constituency shards."""

from __future__ import annotations

import gzip
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

from assam_rolls import schema

from . import extract, output, vision_part

EXPECTED_ACS = tuple(range(1, 127))
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DOMAINS = {
    "lang": {"ASM", "BEN", "ENG"},
    "relation_type": {"", "father", "husband", "mother"},
    "roll_section": {"main", "addition", "deletion"},
    "sex": {"", "F", "M", "T"},
    "status_code": {"", "E", "M", "Q", "R", "S"},
}
STATIC = {
    "roll_type": {"FinalRoll"},
    "revision": {1},
    "year": {2026},
    "engine": {extract.MIXED_ENGINE},
    "pipeline_version": {extract.PIPELINE_VERSION},
}
POPULATED = ("name", "age", "sex", "house_no", "epic_no")


class ReleaseError(ValueError):
    """A release invariant failed."""


@dataclass(frozen=True)
class ReleaseReport:
    """Facts established by a successful build."""

    rows: int
    parts: int
    acs: int
    bytes: int
    sha256: str


def authoritative_parts(path: Path) -> Set[Tuple[int, int]]:
    """Filename-derived AC and part keys from the independently extracted info pages."""
    found: Set[Tuple[int, int]] = set()
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            key = schema.source_file_key(row)
            if key in found:
                raise ReleaseError(f"duplicate authoritative part key {key}")
            found.add(key)
    return found


def authoritative_totals(path: Path) -> Dict[Tuple[int, int], int]:
    """Filename-derived part keys mapped to the info page's published net total."""
    totals: Dict[Tuple[int, int], int] = {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("electors_total") is not None:
                totals[schema.source_file_key(row)] = row["electors_total"]
    return totals


def shard_paths(directory: Path, expected_acs: Sequence[int] = EXPECTED_ACS) -> List[Path]:
    """The exact ordered shard set, refusing missing or unexpected constituency files."""
    expected = [directory / f"AC{ac_no:03d}.parquet" for ac_no in expected_acs]
    missing = [path.name for path in expected if not path.exists()]
    extras = sorted(path.name for path in directory.glob("AC*.parquet") if path not in expected)
    if missing or extras:
        raise ReleaseError(f"shard set differs: missing={missing}, unexpected={extras}")
    return expected


def _values(table: pa.Table, name: str) -> List:
    return table.column(name).to_pylist()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def verify_shard(
    path: Path,
    ac_no: int,
    expected_parts: Set[int],
    expected_net_totals: Dict[int, int],
) -> int:
    """Check one shard's bytes, schema, coverage, provenance, and parser smoke alarms."""
    parquet = pq.ParquetFile(path)
    _require(parquet.schema_arrow == output.SCHEMA, f"{path.name}: schema differs")
    table = parquet.read()
    rows = table.num_rows
    _require(rows > 0, f"{path.name}: empty shard")

    entry_path = path.with_suffix(".entry.json")
    _require(entry_path.exists(), f"{path.name}: missing entry file")
    entry = json.loads(entry_path.read_text(encoding="utf-8"))
    _require(entry.get("rows") == rows, f"{path.name}: entry row count differs")
    _require(entry.get("bytes") == path.stat().st_size, f"{path.name}: entry byte count differs")
    _require(entry.get("sha256") == output.sha256_file(path), f"{path.name}: entry SHA-256 differs")

    acs = set(_values(table, "ac_no"))
    _require(acs == {ac_no}, f"{path.name}: row ACs are {sorted(acs)}")
    parts = set(_values(table, "part_no"))
    _require(parts == expected_parts, f"{path.name}: part keys differ from info pages")
    _require(entry.get("parts") == len(expected_parts), f"{path.name}: entry part count differs")
    _require(
        entry.get("parts_published") == len(expected_net_totals),
        f"{path.name}: entry published-total count differs",
    )
    _require(entry.get("parts_failed") == 0, f"{path.name}: entry records failed parts")

    verify_path = path.with_suffix(".verify.json")
    _require(verify_path.exists(), f"{path.name}: missing verification bundle")
    bundle = json.loads(verify_path.read_text(encoding="utf-8"))
    _require(
        bundle.get("published_parts") == len(expected_net_totals),
        f"{path.name}: verification bundle published-total count differs",
    )
    _require(bundle.get("rows") == rows, f"{path.name}: verification bundle row count differs")
    boxes = {int(part): count for part, count in bundle.get("boxes_per_part", {}).items()}
    _require(set(boxes) == expected_parts, f"{path.name}: box evidence does not cover every part")
    rows_per_part = Counter(_values(table, "part_no"))
    lost = {
        part: (boxes[part], rows_per_part[part])
        for part in sorted(parts)
        if boxes[part] != rows_per_part[part]
    }
    _require(not lost, f"{path.name}: stage-one boxes differ from rows: {lost}")
    printed = {int(part): count for part, count in bundle.get("printed_totals", {}).items()}
    _require(set(printed) <= expected_parts, f"{path.name}: printed totals name unknown parts")
    _require(
        all(isinstance(count, int) and count > 0 for count in printed.values()),
        f"{path.name}: invalid printed total",
    )

    columns = table.to_pydict()

    reconciliation_fields = {
        "parts_measured",
        "parts_matching_roll",
        "rows_short_of_printed",
        "struck_off_detected",
        "struck_off_implied",
    }
    missing_reconciliation = reconciliation_fields - set(entry)
    _require(
        not missing_reconciliation,
        f"{path.name}: entry lacks reconciliation fields {sorted(missing_reconciliation)}",
    )
    main = Counter(
        part
        for part, section in zip(columns["part_no"], columns["roll_section"])
        if section == "main"
    )
    addition = Counter(
        part
        for part, section in zip(columns["part_no"], columns["roll_section"])
        if section == "addition"
    )
    usable = {part: total for part, total in printed.items() if abs(main[part] - total) <= 2}
    expected_reconciliation = {
        "parts_measured": len(usable),
        "parts_matching_roll": sum(main[part] == total for part, total in usable.items()),
        "rows_short_of_printed": sum(abs(main[part] - total) for part, total in usable.items()),
        "struck_off_detected": sum(columns["deleted"]),
        "struck_off_implied": sum(
            main[part] + addition[part] - net for part, net in expected_net_totals.items()
        ),
    }
    for name, value in expected_reconciliation.items():
        _require(entry.get(name) == value, f"{path.name}: entry {name} differs")

    source_order = list(
        zip(columns["part_no"], columns["page_no"], columns["box_row"], columns["box_col"])
    )
    _require(source_order == sorted(source_order), f"{path.name}: rows are out of source order")
    serials: Dict[int, int] = {}
    for part, serial in zip(columns["part_no"], columns["serial_no"]):
        serials[part] = serials.get(part, 0) + 1
        _require(serial == serials[part], f"{path.name}: serial sequence breaks in part {part}")
    keys = zip(
        columns["part_no"],
        columns["page_no"],
        columns["box_row"],
        columns["box_col"],
    )
    _require(len(set(keys)) == rows, f"{path.name}: duplicate source-box keys")
    sources = set(
        zip(
            columns["part_no"],
            columns["source_zip"],
            columns["source_pdf"],
            columns["pdf_sha256"],
            columns["lang"],
            columns["roll_type"],
            columns["revision"],
            columns["year"],
        )
    )
    _require(len(sources) == len(parts), f"{path.name}: parts do not map one-to-one to PDFs")
    _require(
        len({source[3] for source in sources}) == len(parts),
        f"{path.name}: different parts share source PDF bytes",
    )
    languages = set(columns["lang"])
    _require(len(languages) == 1, f"{path.name}: constituency mixes languages")
    for part, source_zip, source_pdf, _, lang, roll_type, revision, year in sources:
        meta = schema.parse_source_filename(source_pdf)
        _require(meta is not None, f"{path.name}: malformed source PDF name {source_pdf!r}")
        expected = {
            "ac_no": ac_no,
            "part_no": part,
            "lang": lang,
            "roll_type": roll_type,
            "revision": revision,
            "year": year,
        }
        _require(
            all(meta.get(name) == value for name, value in expected.items()),
            f"{path.name}: source PDF identity differs for part {part}",
        )
        _require(
            source_zip == f"AC{ac_no}_{lang}.zip",
            f"{path.name}: source ZIP identity differs for part {part}",
        )

    for name, allowed in {**DOMAINS, **STATIC}.items():
        values = set(columns[name])
        _require(values <= allowed, f"{path.name}: unexpected {name}: {sorted(values - allowed)}")
    for name in POPULATED:
        filled = sum(value not in (None, "", 0) for value in columns[name]) / rows
        _require(filled >= 0.5, f"{path.name}: {name} only {filled:.1%} populated")
        by_part = {part: [0, 0] for part in parts}
        for part, value in zip(columns["part_no"], columns[name]):
            by_part[part][1] += 1
            by_part[part][0] += value not in (None, "", 0)
        for part, (part_filled, part_rows) in by_part.items():
            rate = part_filled / part_rows
            _require(
                rate >= 0.5,
                f"{path.name}: part {part} {name} only {rate:.1%} populated",
            )

    _require(all(value and value > 0 for value in columns["serial_no"]), f"{path.name}: bad serial")
    _require(all(value and value > 0 for value in columns["page_no"]), f"{path.name}: bad page")
    _require(all(value in range(10) for value in columns["box_row"]), f"{path.name}: bad box row")
    _require(
        all(value in (0, 1, 2) for value in columns["box_col"]), f"{path.name}: bad box column"
    )
    _require(
        all(value is None or 18 <= value <= 120 for value in columns["age"]),
        f"{path.name}: impossible age",
    )
    _require(
        all(
            value is None or 0 < value <= vision_part.MAX_SERIAL
            for value in columns["serial_no_ocr"]
        ),
        f"{path.name}: implausible OCR serial",
    )
    _require(
        all(
            bool(code) == deleted
            for code, deleted in zip(columns["status_code"], columns["deleted"])
        ),
        f"{path.name}: status/deleted disagreement",
    )
    _require(
        all(
            section != "deletion" or not epic
            for section, epic in zip(columns["roll_section"], columns["epic_no"])
        ),
        f"{path.name}: deletion rows carry EPICs",
    )
    _require(
        all(
            bool(flags) == review
            for flags, review in zip(columns["flags"], columns["needs_review"])
        ),
        f"{path.name}: flags/review disagreement",
    )
    _require(
        all(value and SHA256.fullmatch(value) for value in columns["pdf_sha256"]),
        f"{path.name}: malformed PDF SHA-256",
    )
    _require(
        all(columns[name][i] for name in ("source_zip", "source_pdf") for i in range(rows)),
        f"{path.name}: blank source path",
    )
    try:
        for value in columns["extracted_at"]:
            parsed = datetime.fromisoformat(value)
            _require(parsed.utcoffset() is not None, f"{path.name}: naive extraction timestamp")
    except (TypeError, ValueError) as exc:
        raise ReleaseError(f"{path.name}: malformed extraction timestamp") from exc
    return rows


def verify_shards(
    directory: Path,
    parts_file: Path,
    expected_acs: Sequence[int] = EXPECTED_ACS,
) -> Tuple[List[Path], int, int]:
    """Verify every input shard and its exact statewide part coverage."""
    expected = authoritative_parts(parts_file)
    totals = authoritative_totals(parts_file)
    wanted_acs = set(expected_acs)
    found_acs = {ac_no for ac_no, _ in expected}
    _require(
        found_acs == wanted_acs,
        "authoritative AC set differs: "
        f"missing={sorted(wanted_acs - found_acs)}, "
        f"unexpected={sorted(found_acs - wanted_acs)}",
    )
    paths = shard_paths(directory, expected_acs)
    total = 0
    observed: Set[Tuple[int, int]] = set()
    source_hashes: Dict[str, Tuple[int, int]] = {}
    for ac_no, path in zip(expected_acs, paths):
        ac_parts = {part for ac, part in expected if ac == ac_no}
        ac_totals = {part: total for (ac, part), total in totals.items() if ac == ac_no}
        _require(ac_parts, f"AC{ac_no:03d}: no authoritative parts")
        total += verify_shard(path, ac_no, ac_parts, ac_totals)
        source_rows = pq.read_table(path, columns=["part_no", "pdf_sha256"]).to_pydict()
        for part, digest in set(zip(source_rows["part_no"], source_rows["pdf_sha256"])):
            previous = source_hashes.get(digest)
            _require(
                previous is None,
                f"source PDF bytes shared by parts {previous} and {(ac_no, part)}",
            )
            source_hashes[digest] = (ac_no, part)
        observed.update((ac_no, part) for part in ac_parts)
    _require(observed == expected, "statewide part coverage differs from info pages")
    return paths, total, len(observed)


def _write_checksum(path: Path, digest: str) -> None:
    target = path.with_suffix(".sha256")
    temporary = target.with_suffix(".sha256.tmp")
    temporary.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    temporary.replace(target)


def verify_merged(path: Path, shards: Iterable[Path]) -> int:
    """Read the finished file back and prove that every shard survived byte-for-value."""
    merged = pq.ParquetFile(path)
    paths = list(shards)
    _require(merged.schema_arrow == output.SCHEMA, f"{path.name}: schema differs")
    _require(merged.num_row_groups == len(paths), f"{path.name}: row-group count differs")
    total = 0
    for index, shard in enumerate(paths):
        source = pq.read_table(shard)
        assembled = merged.read_row_group(index)
        _require(
            assembled.equals(source), f"{path.name}: row group {index} differs from {shard.name}"
        )
        total += source.num_rows
    _require(merged.metadata.num_rows == total, f"{path.name}: total row count differs")
    return total


def build(
    directory: Path,
    parts_file: Path,
    destination: Path,
    expected_acs: Sequence[int] = EXPECTED_ACS,
) -> ReleaseReport:
    """Validate, assemble atomically, verify from disk, and checksum the release."""
    paths, rows, parts = verify_shards(directory, parts_file, expected_acs)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".parquet.tmp")
    writer = pq.ParquetWriter(temporary, output.SCHEMA, compression="zstd")
    try:
        for path in paths:
            table = pq.read_table(path)
            writer.write_table(table, row_group_size=table.num_rows)
    finally:
        writer.close()
    _require(verify_merged(temporary, paths) == rows, "release row count changed after writing")
    digest = output.sha256_file(temporary)
    temporary.replace(destination)
    _write_checksum(destination, digest)
    return ReleaseReport(rows, parts, len(expected_acs), destination.stat().st_size, digest)
