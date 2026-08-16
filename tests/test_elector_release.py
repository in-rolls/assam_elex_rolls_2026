import gzip
import json
from collections import Counter

import pyarrow.parquet as pq
import pytest

from electors import extract, output, release


def row(ac_no, part_no, serial_no=1, **overrides):
    found = {
        "ac_no": ac_no,
        "part_no": part_no,
        "serial_no": serial_no,
        "epic_no": f"ABC{ac_no:03d}{serial_no:04d}",
        "name": "নাম",
        "relation_name": "সম্বন্ধ",
        "relation_type": "father",
        "house_no": "1",
        "age": 30,
        "sex": "M",
        "deleted": False,
        "status_code": "",
        "lang": "ASM",
        "roll_section": "main",
        "page_no": 3,
        "box_row": serial_no - 1,
        "box_col": 0,
        "serial_no_ocr": serial_no,
        "flags": "",
        "needs_review": False,
        "source_zip": f"AC{ac_no}_ASM.zip",
        "source_pdf": (f"2026-EROLLGEN-S03-{ac_no}-FinalRoll-Revision1-ASM-{part_no}-WI.pdf"),
        "pdf_sha256": f"{ac_no * 10_000 + part_no:064x}",
        "roll_type": "FinalRoll",
        "revision": 1,
        "year": 2026,
        "engine": extract.MIXED_ENGINE,
        "pipeline_version": extract.PIPELINE_VERSION,
        "extracted_at": "2026-08-15T12:00:00+00:00",
    }
    found.update(overrides)
    return found


def write_shard(directory, ac_no, rows):
    shard = output.write_shard(rows, ac_no, directory)
    counts = Counter(found["part_no"] for found in rows)
    output.write_entry(
        ac_no,
        shard,
        {
            "rows": len(rows),
            "parts": len(counts),
            "parts_published": len(counts),
            "parts_measured": 0,
            "parts_matching_roll": 0,
            "rows_short_of_printed": 0,
            "parts_failed": 0,
            "struck_off_detected": sum(found["deleted"] for found in rows),
            "struck_off_implied": 0,
        },
        directory,
    )
    shard.with_suffix(".verify.json").write_text(
        json.dumps(
            {
                "published_parts": len(counts),
                "printed_totals": {},
                "boxes_per_part": {str(part): count for part, count in counts.items()},
                "rows": len(rows),
            }
        )
    )
    return shard


def write_parts(path, keys, missing_totals=()):
    totals = Counter()
    for shard in (path.parent / "shards").glob("AC*.parquet"):
        table = pq.read_table(shard, columns=["ac_no", "part_no", "roll_section"]).to_pydict()
        totals.update(
            (ac_no, part_no)
            for ac_no, part_no, section in zip(
                table["ac_no"], table["part_no"], table["roll_section"]
            )
            if section in ("main", "addition")
        )
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for ac_no, part_no in keys:
            row = {"ac_no_file": ac_no, "part_no_file": part_no}
            if (ac_no, part_no) not in missing_totals:
                row["electors_total"] = totals[(ac_no, part_no)]
            handle.write(json.dumps(row) + "\n")


def test_release_is_the_exact_ordered_concatenation_of_verified_shards(tmp_path):
    shards = tmp_path / "shards"
    first = write_shard(shards, 1, [row(1, 1), row(1, 1, 2)])
    second = write_shard(shards, 2, [row(2, 4)])
    parts = tmp_path / "parts.jsonl.gz"
    write_parts(parts, [(1, 1), (2, 4)])

    destination = tmp_path / "state.parquet"
    report = release.build(shards, parts, destination, expected_acs=(1, 2))

    assert (report.rows, report.parts, report.acs) == (3, 2, 2)
    assert destination.exists()
    assert destination.with_suffix(".sha256").read_text().endswith("  state.parquet\n")
    assert release.verify_merged(destination, [first, second]) == 3
    assert pq.ParquetFile(destination).num_row_groups == 2


def test_release_refuses_a_part_missing_from_the_shard(tmp_path):
    shards = tmp_path / "shards"
    write_shard(shards, 1, [row(1, 1)])
    parts = tmp_path / "parts.jsonl.gz"
    write_parts(parts, [(1, 1), (1, 2)])

    with pytest.raises(release.ReleaseError, match="part keys differ"):
        release.build(shards, parts, tmp_path / "state.parquet", expected_acs=(1,))


def test_release_refuses_authoritative_records_outside_the_expected_ac_set(tmp_path):
    shards = tmp_path / "shards"
    write_shard(shards, 1, [row(1, 1)])
    parts = tmp_path / "parts.jsonl.gz"
    write_parts(parts, [(1, 1), (127, 1)])

    with pytest.raises(release.ReleaseError, match="authoritative AC set differs"):
        release.build(shards, parts, tmp_path / "state.parquet", expected_acs=(1,))


def test_release_refuses_a_parser_that_left_a_field_empty(tmp_path):
    shards = tmp_path / "shards"
    write_shard(shards, 1, [row(1, 1, name="")])
    parts = tmp_path / "parts.jsonl.gz"
    write_parts(parts, [(1, 1)])

    with pytest.raises(release.ReleaseError, match="name only 0.0% populated"):
        release.build(shards, parts, tmp_path / "state.parquet", expected_acs=(1,))


def test_release_refuses_part_level_field_loss_hidden_by_the_constituency_average(tmp_path):
    shards = tmp_path / "shards"
    write_shard(
        shards,
        1,
        [row(1, 1), row(1, 1, 2), row(1, 2, name="")],
    )
    parts = tmp_path / "parts.jsonl.gz"
    write_parts(parts, [(1, 1), (1, 2)])

    with pytest.raises(release.ReleaseError, match="part 2 name only 0.0% populated"):
        release.build(shards, parts, tmp_path / "state.parquet", expected_acs=(1,))


def test_release_refuses_different_parts_backed_by_the_same_pdf_bytes(tmp_path):
    shards = tmp_path / "shards"
    digest = "a" * 64
    write_shard(
        shards,
        1,
        [row(1, 1, pdf_sha256=digest), row(1, 2, pdf_sha256=digest)],
    )
    parts = tmp_path / "parts.jsonl.gz"
    write_parts(parts, [(1, 1), (1, 2)])

    with pytest.raises(release.ReleaseError, match="different parts share source PDF bytes"):
        release.build(shards, parts, tmp_path / "state.parquet", expected_acs=(1,))


def test_release_refuses_the_same_pdf_bytes_across_constituencies(tmp_path):
    shards = tmp_path / "shards"
    digest = "a" * 64
    write_shard(shards, 1, [row(1, 1, pdf_sha256=digest)])
    write_shard(shards, 2, [row(2, 1, pdf_sha256=digest)])
    parts = tmp_path / "parts.jsonl.gz"
    write_parts(parts, [(1, 1), (2, 1)])

    with pytest.raises(release.ReleaseError, match="source PDF bytes shared by parts"):
        release.build(shards, parts, tmp_path / "state.parquet", expected_acs=(1, 2))


def test_release_refuses_duplicate_source_boxes(tmp_path):
    shards = tmp_path / "shards"
    write_shard(shards, 1, [row(1, 1), row(1, 1, 2, box_row=0)])
    parts = tmp_path / "parts.jsonl.gz"
    write_parts(parts, [(1, 1)])

    with pytest.raises(release.ReleaseError, match="duplicate source-box keys"):
        release.build(shards, parts, tmp_path / "state.parquet", expected_acs=(1,))


def test_release_refuses_a_stage_one_box_that_never_became_a_row(tmp_path):
    shards = tmp_path / "shards"
    shard = write_shard(shards, 1, [row(1, 1)])
    bundle = json.loads(shard.with_suffix(".verify.json").read_text())
    bundle["boxes_per_part"]["1"] = 2
    shard.with_suffix(".verify.json").write_text(json.dumps(bundle))
    parts = tmp_path / "parts.jsonl.gz"
    write_parts(parts, [(1, 1)])

    with pytest.raises(release.ReleaseError, match="stage-one boxes differ from rows"):
        release.build(shards, parts, tmp_path / "state.parquet", expected_acs=(1,))


def test_release_refuses_a_serial_sequence_that_restarts(tmp_path):
    shards = tmp_path / "shards"
    write_shard(shards, 1, [row(1, 1), row(1, 1, 1, box_row=1)])
    parts = tmp_path / "parts.jsonl.gz"
    write_parts(parts, [(1, 1)])

    with pytest.raises(release.ReleaseError, match="serial sequence breaks"):
        release.build(shards, parts, tmp_path / "state.parquet", expected_acs=(1,))


def test_release_refuses_a_deletion_row_with_an_epic(tmp_path):
    shards = tmp_path / "shards"
    write_shard(shards, 1, [row(1, 1, roll_section="deletion")])
    parts = tmp_path / "parts.jsonl.gz"
    write_parts(parts, [(1, 1)])

    with pytest.raises(release.ReleaseError, match="deletion rows carry EPICs"):
        release.build(shards, parts, tmp_path / "state.parquet", expected_acs=(1,))


def test_release_refuses_source_provenance_that_disagrees_with_the_row(tmp_path):
    shards = tmp_path / "shards"
    write_shard(
        shards,
        1,
        [row(1, 1, source_pdf="2026-EROLLGEN-S03-99-FinalRoll-Revision1-ASM-1-WI.pdf")],
    )
    parts = tmp_path / "parts.jsonl.gz"
    write_parts(parts, [(1, 1)])

    with pytest.raises(release.ReleaseError, match="source PDF identity differs"):
        release.build(shards, parts, tmp_path / "state.parquet", expected_acs=(1,))


def test_release_refuses_an_entry_that_does_not_describe_its_shard(tmp_path):
    shards = tmp_path / "shards"
    shard = write_shard(shards, 1, [row(1, 1)])
    entry = json.loads(shard.with_suffix(".entry.json").read_text())
    entry["sha256"] = "0" * 64
    shard.with_suffix(".entry.json").write_text(json.dumps(entry))
    parts = tmp_path / "parts.jsonl.gz"
    write_parts(parts, [(1, 1)])

    with pytest.raises(release.ReleaseError, match="entry SHA-256 differs"):
        release.build(shards, parts, tmp_path / "state.parquet", expected_acs=(1,))


def test_release_distinguishes_source_parts_from_parts_with_published_totals(tmp_path):
    shards = tmp_path / "shards"
    shard = write_shard(shards, 1, [row(1, 1), row(1, 2)])
    bundle = json.loads(shard.with_suffix(".verify.json").read_text())
    bundle["published_parts"] = 1
    shard.with_suffix(".verify.json").write_text(json.dumps(bundle))
    entry = json.loads(shard.with_suffix(".entry.json").read_text())
    entry["parts_published"] = 1
    shard.with_suffix(".entry.json").write_text(json.dumps(entry))
    parts = tmp_path / "parts.jsonl.gz"
    write_parts(parts, [(1, 1), (1, 2)], missing_totals={(1, 2)})

    report = release.build(shards, parts, tmp_path / "state.parquet", expected_acs=(1,))

    assert report.parts == 2


def test_release_refuses_stale_reconciliation_metadata(tmp_path):
    shards = tmp_path / "shards"
    shard = write_shard(shards, 1, [row(1, 1)])
    bundle = json.loads(shard.with_suffix(".verify.json").read_text())
    bundle["printed_totals"] = {"1": 1}
    shard.with_suffix(".verify.json").write_text(json.dumps(bundle))
    entry = json.loads(shard.with_suffix(".entry.json").read_text())
    entry.update(
        {
            "parts_measured": 0,
            "parts_matching_roll": 1,
            "rows_short_of_printed": 0,
            "struck_off_detected": 0,
            "struck_off_implied": 0,
        }
    )
    shard.with_suffix(".entry.json").write_text(json.dumps(entry))
    parts = tmp_path / "parts.jsonl.gz"
    write_parts(parts, [(1, 1)])

    with pytest.raises(release.ReleaseError, match="entry parts_measured differs"):
        release.build(shards, parts, tmp_path / "state.parquet", expected_acs=(1,))


def test_release_refuses_missing_reconciliation_metadata(tmp_path):
    shards = tmp_path / "shards"
    shard = write_shard(shards, 1, [row(1, 1)])
    entry = json.loads(shard.with_suffix(".entry.json").read_text())
    entry.pop("struck_off_detected")
    shard.with_suffix(".entry.json").write_text(json.dumps(entry))
    parts = tmp_path / "parts.jsonl.gz"
    write_parts(parts, [(1, 1)])

    with pytest.raises(release.ReleaseError, match="entry lacks reconciliation fields"):
        release.build(shards, parts, tmp_path / "state.parquet", expected_acs=(1,))


def test_release_refuses_incorrect_ocr_engine_provenance(tmp_path):
    shards = tmp_path / "shards"
    write_shard(shards, 1, [row(1, 1, engine="tesseract")])
    parts = tmp_path / "parts.jsonl.gz"
    write_parts(parts, [(1, 1)])

    with pytest.raises(release.ReleaseError, match="unexpected engine"):
        release.build(shards, parts, tmp_path / "state.parquet", expected_acs=(1,))
