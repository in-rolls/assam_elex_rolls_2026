"""Output writers: encoding, null semantics, and round-tripping.

The properties tested here are the ones that quietly break in the hands of a downstream
user rather than in CI -- a missing BOM turns Assamese into mojibake only once someone
opens the file in Excel, and a null written as an empty string is indistinguishable from
a genuinely blank field forever after.
"""

from __future__ import annotations

import csv
import json
import unicodedata

from assam_rolls import output
from assam_rolls.schema import SECTION_COLUMNS

COLUMNS = ["ac_no_file", "part_no_file", "district", "ward_no", "electors_total"]

ASSAMESE = "যোৰহাট"


def part(**overrides):
    row = {
        "ac_no_file": 100,
        "part_no_file": 1,
        "district": ASSAMESE,
        "ward_no": "",
        "electors_total": 942,
    }
    row.update(overrides)
    return row


class TestJsonl:
    def test_assamese_is_not_escaped(self, tmp_path):
        path = tmp_path / "parts.jsonl"
        output.write_jsonl(path, [part()], columns=COLUMNS)
        raw = path.read_text(encoding="utf-8")
        assert ASSAMESE in raw
        assert "\\u" not in raw

    def test_text_is_nfc_normalised(self, tmp_path):
        path = tmp_path / "parts.jsonl"
        output.write_jsonl(path, [part()], columns=COLUMNS)
        value = output.read_jsonl(path)[0]["district"]
        assert value == unicodedata.normalize("NFC", value)

    def test_types_survive_the_round_trip(self, tmp_path):
        """An integer must come back an integer, not the string "942"."""
        path = tmp_path / "parts.jsonl"
        output.write_jsonl(path, [part()], columns=COLUMNS)
        record = output.read_jsonl(path)[0]
        assert record["electors_total"] == 942
        assert isinstance(record["electors_total"], int)

    def test_null_and_blank_stay_distinct(self, tmp_path):
        path = tmp_path / "parts.jsonl"
        rows = [part(ward_no=""), part(part_no_file=2, ward_no=None)]
        output.write_jsonl(path, rows, columns=COLUMNS)
        blank, unread = output.read_jsonl(path)
        assert blank["ward_no"] == ""
        assert unread["ward_no"] is None

    def test_sections_nest_inside_their_part(self, tmp_path):
        path = tmp_path / "parts.jsonl"
        sections = [
            {"ac_no": 100, "part_no": 1, "section_no": 1, "section_name": "পকিমুৰী"},
            {"ac_no": 100, "part_no": 1, "section_no": 2, "section_name": "হাবি গাওঁ"},
            {"ac_no": 100, "part_no": 2, "section_no": 1, "section_name": "অন্য"},
        ]
        output.write_jsonl(path, [part()], sections, columns=COLUMNS)
        record = output.read_jsonl(path)[0]
        assert [s["section_no"] for s in record["sections"]] == [1, 2]
        # The join keys are the part's own; repeating them inside each section is noise.
        assert "ac_no" not in record["sections"][0]

    def test_each_line_is_independently_parseable(self, tmp_path):
        path = tmp_path / "parts.jsonl"
        output.write_jsonl(path, [part(), part(part_no_file=2)], columns=COLUMNS)
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert all(json.loads(line) for line in lines)

    def test_flatten_inverts_the_nesting(self, tmp_path):
        path = tmp_path / "parts.jsonl"
        sections = [{"ac_no": 100, "part_no": 1, "section_no": 1, "section_name": "ক"}]
        output.write_jsonl(path, [part()], sections, columns=COLUMNS)
        parts, recovered = output.flatten(output.read_jsonl(path))
        assert len(parts) == 1 and "sections" not in parts[0]
        assert recovered[0]["ac_no"] == 100
        assert recovered[0]["part_no"] == 1
        assert recovered[0]["section_name"] == "ক"


class TestCsv:
    def test_starts_with_the_utf8_bom(self, tmp_path):
        """Without this, Excel renders every Assamese field as mojibake."""
        path = tmp_path / "parts.csv"
        output.write_csv(path, [part()], COLUMNS)
        assert path.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_standard_readers_strip_the_bom(self, tmp_path):
        path = tmp_path / "parts.csv"
        output.write_csv(path, [part()], COLUMNS)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle))
        assert row["ac_no_file"] == "100"
        assert row["district"] == ASSAMESE

    def test_null_becomes_na_and_blank_stays_empty(self, tmp_path):
        path = tmp_path / "parts.csv"
        output.write_csv(path, [part(ward_no=None, district="")], COLUMNS)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle))
        assert row["ward_no"] == output.CSV_NULL == "NA"
        assert row["district"] == ""

    def test_missing_column_is_reported_as_unread(self, tmp_path):
        """A column absent from the row is not blank -- it was never read."""
        path = tmp_path / "parts.csv"
        output.write_csv(path, [{"ac_no_file": 100}], COLUMNS)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle))
        assert row["district"] == "NA"

    def test_column_order_is_the_requested_order(self, tmp_path):
        path = tmp_path / "parts.csv"
        output.write_csv(path, [part()], COLUMNS)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            assert next(csv.reader(handle)) == COLUMNS


class TestWriteDataset:
    def test_writes_all_three_files(self, tmp_path):
        sections = [{"ac_no": 100, "part_no": 1, "section_no": 1, "section_name": "ক"}]
        counts = output.write_dataset(tmp_path, [part()], sections, COLUMNS)
        assert counts == {"parts.jsonl": 1, "parts.csv": 1, "part_sections.csv": 1}
        for name in counts:
            assert (tmp_path / name).exists()

    def test_sections_csv_carries_the_join_keys(self, tmp_path):
        """The flat form only works if sections can be joined back to their part."""
        sections = [{"ac_no": 100, "part_no": 1, "section_no": 1, "section_name": "ক"}]
        output.write_dataset(tmp_path, [part()], sections, COLUMNS)
        with (tmp_path / "part_sections.csv").open(encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle))
        assert row["ac_no"] == "100"
        assert row["part_no"] == "1"
        assert set(SECTION_COLUMNS) <= set(row)

    def test_jsonl_and_csv_describe_the_same_parts(self, tmp_path):
        rows = [part(), part(part_no_file=2, ward_no=None)]
        output.write_dataset(tmp_path, rows, [], COLUMNS)
        records = output.read_jsonl(tmp_path / "parts.jsonl")
        with (tmp_path / "parts.csv").open(encoding="utf-8-sig", newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
        assert len(records) == len(csv_rows) == 2
        for record, flat in zip(records, csv_rows):
            expected = "NA" if record["ward_no"] is None else record["ward_no"]
            assert flat["ward_no"] == expected
