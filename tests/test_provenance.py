"""Provenance: can every row be traced back to the bytes it came from?

The dataset is only trustworthy if a reader can take any row, find the exact PDF it was
read from, and confirm the file has not changed since. That requires the source zip, the
PDF name inside it, and a hash of that PDF's bytes -- and requires them to be populated
on *every* row, including rows the pipeline failed to parse.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from assam_rolls import render, schema
from assam_rolls.parse import failed_row
from assam_rolls.render import PartRef

ZIP_DIR = Path("data/ac_info")


def sample_ref():
    return PartRef(
        zip_name="AC100_ASM Roll Info Pages.zip",
        pdf_name="2026-EROLLGEN-S03-100-FinalRoll-Revision1-ASM-1-WI_INFO.pdf",
        ac_no=100,
        part_no=1,
    )


class TestFilenameProvenance:
    def test_every_filename_field_is_recovered(self):
        parsed = schema.parse_source_filename(sample_ref().pdf_name)
        assert parsed == {
            "info_pages": True,
            "year": 2026,
            "state": "S03",
            "ac_no": 100,
            "part_no": 1,
            "roll_type": "FinalRoll",
            "revision": 1,
            "lang": "ASM",
        }

    def test_unrecognized_name_is_reported_not_guessed(self):
        assert schema.parse_source_filename("some_other_document.pdf") is None


class TestFailedRowProvenance:
    """A page the parser could not read must still be traceable."""

    def test_carries_full_provenance(self):
        provenance = {
            "source_zip_dir": "data/ac_info",
            "pdf_sha256": "a" * 64,
            "pdf_bytes": 1170600,
            "page_png": "out/pages/100-0001.png",
            "page_sha256": "b" * 64,
            "engine_version": "tesseract 5.5.2 (asm=synth20170629)",
        }
        row = failed_row(sample_ref(), "tesseract", "no stable rules found", provenance)
        for column in schema.PROVENANCE_COLUMNS:
            assert row[column] not in (None, ""), f"{column} is empty on a failed row"

    def test_is_flagged_for_review_with_a_reason(self):
        row = failed_row(sample_ref(), "tesseract", "no stable rules found", {})
        assert row["needs_review"] is True
        assert row["flags"] == "layout_failed"
        assert "stable rules" in row["anomaly_notes"]

    def test_appears_in_the_dataset_rather_than_vanishing(self):
        row = failed_row(sample_ref(), "tesseract", "boom", {})
        assert row["ac_no_file"] == 100
        assert row["part_no_file"] == 1


class TestSchemaGroups:
    def test_provenance_columns_are_all_output_columns(self):
        assert set(schema.PROVENANCE_COLUMNS) <= set(schema.OUTPUT_COLUMNS)

    def test_output_drops_what_ocr_cannot_produce(self):
        """Shipping always-empty columns would imply data we do not have."""
        for column in schema.UNSUPPORTED_BY_OCR:
            assert column not in schema.OUTPUT_COLUMNS

    def test_no_duplicate_columns(self):
        assert len(schema.OUTPUT_COLUMNS) == len(set(schema.OUTPUT_COLUMNS))

    def test_empty_row_means_unread_not_blank(self):
        """``None`` is the honest default: nothing has been read yet."""
        assert set(schema.empty_part_row().values()) == {None}


# ------------------------------------------------------------ against the real corpus

pytestmark_corpus = pytest.mark.skipif(not ZIP_DIR.exists(), reason="source zips not present")


@pytestmark_corpus
class TestStitchingRoundTrip:
    """Take the identifiers a row carries and get back to the original bytes."""

    def build_row(self):
        """A row carrying exactly the provenance the pipeline writes."""
        zip_path = ZIP_DIR / sample_ref().zip_name
        if not zip_path.exists():
            pytest.skip(f"{zip_path} not present")
        payload = render.read_pdf_bytes(zip_path, sample_ref().pdf_name)
        return failed_row(
            sample_ref(),
            "tesseract",
            "constructed for the round trip",
            {
                "source_zip_dir": str(ZIP_DIR),
                "pdf_sha256": render.sha256_bytes(payload),
                "pdf_bytes": len(payload),
                "page_png": "out/pages/100-0001.png",
                "page_sha256": "b" * 64,
                "engine_version": "tesseract",
            },
        )

    def test_a_row_leads_back_to_its_bytes(self):
        """Using only the row's own columns -- no outside knowledge of where it came from."""
        row = self.build_row()

        located = Path(row["source_zip_dir"]) / row["source_zip"]
        assert located.exists(), "source_zip_dir + source_zip must locate the archive"
        with zipfile.ZipFile(located) as archive:
            assert row["source_pdf"] in archive.namelist()
            recovered = archive.read(row["source_pdf"])

        assert len(recovered) == row["pdf_bytes"]
        assert hashlib.sha256(recovered).hexdigest() == row["pdf_sha256"]

    def test_a_changed_source_would_be_detected(self):
        """The hash is only useful if it fails when the bytes differ."""
        row = self.build_row()
        tampered = hashlib.sha256(b"a re-issued pdf").hexdigest()
        assert tampered != row["pdf_sha256"]

    def test_identifiers_agree_with_the_filename(self):
        row = self.build_row()
        parsed = schema.parse_source_filename(row["source_pdf"])
        assert parsed["ac_no"] == row["ac_no_file"]
        assert parsed["part_no"] == row["part_no_file"]
        assert parsed["year"] == row["roll_year"]
