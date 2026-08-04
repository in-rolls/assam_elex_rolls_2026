"""Tests for the consistency checks.

Every check gets both a passing and a failing case, built from synthetic rows so the
failure modes are exercised deliberately rather than waiting to appear in real data.
"""

import pytest

from assam_rolls import validate


def good_row(**overrides):
    """A row that passes every check; tests override one field at a time."""
    row = {
        "ac_no": 1,
        "part_no": 3,
        "ac_no_file": 1,
        "part_no_file": 3,
        "electors_male": 437,
        "electors_female": 413,
        "electors_third_gender": 0,
        "electors_total": 850,
        "start_serial": 1,
        "end_serial": 874,
        "pin_code": "783350",
        "qualifying_date": "2026-01-01",
        "publication_date": "2026-02-10",
        "revision_year": 2026,
        "district": "কোকৰাঝাৰ",
        "ps_name": "বনগাঁও এল.পি. স্কুল",
        "ps_address": "৫৯০ বনগাঁও এল. পি. স্কুল",
        "template_match": True,
        "extraction_confidence": "HIGH",
    }
    row.update(overrides)
    return row


def result_for(row, name, context=None):
    results = {r.name: r for r in validate.check_row(row, context)}
    assert name in results, f"check {name} did not run"
    return results[name]


class TestFilenameGroundTruth:
    def test_ac_no_agreement_passes(self):
        assert result_for(good_row(), "ac_no_matches_filename").passed

    def test_ac_no_mismatch_is_hard(self):
        check = result_for(good_row(ac_no=2), "ac_no_matches_filename")
        assert not check.passed
        assert check.severity == validate.HARD

    def test_part_no_mismatch_is_hard(self):
        check = result_for(good_row(part_no=99), "part_no_matches_filename")
        assert not check.passed
        assert check.severity == validate.HARD

    def test_missing_page_value_fails(self):
        assert not result_for(good_row(ac_no=None), "ac_no_matches_filename").passed

    def test_string_and_int_compare_equal(self):
        """CSV round-trips turn ints into strings; that must not look like a mismatch."""
        assert result_for(good_row(ac_no="1", ac_no_file="1"), "ac_no_matches_filename").passed


class TestElectorArithmetic:
    def test_sum_matches_total(self):
        assert result_for(good_row(), "gender_sum_matches_total").passed

    def test_sum_mismatch_is_hard(self):
        check = result_for(good_row(electors_total=999), "gender_sum_matches_total")
        assert not check.passed
        assert check.severity == validate.HARD

    def test_missing_counts_fail_rather_than_crash(self):
        check = result_for(good_row(electors_male=None), "gender_sum_matches_total")
        assert not check.passed
        assert "missing" in check.detail

    def test_third_gender_is_counted(self):
        row = good_row(electors_third_gender=2, electors_total=852)
        assert result_for(row, "gender_sum_matches_total").passed


class TestSerials:
    def test_ordered_serials_pass(self):
        assert result_for(good_row(), "serial_order").passed

    def test_reversed_serials_are_hard_failure(self):
        check = result_for(good_row(start_serial=900, end_serial=1), "serial_order")
        assert not check.passed
        assert check.severity == validate.HARD

    def test_span_may_exceed_total_because_of_deletions(self):
        assert result_for(good_row(), "serial_span_covers_total").passed

    def test_span_smaller_than_total_is_suspicious(self):
        check = result_for(good_row(end_serial=10), "serial_span_covers_total")
        assert not check.passed
        assert check.severity == validate.SOFT


class TestPinCode:
    @pytest.mark.parametrize("pin", ["783350", "781001", "790001"])
    def test_assam_pins_pass(self, pin):
        assert result_for(good_row(pin_code=pin), "pin_code_plausible").passed

    @pytest.mark.parametrize("pin", ["110001", "7833", "abcdef", "", None])
    def test_implausible_pins_flagged(self, pin):
        assert not result_for(good_row(pin_code=pin), "pin_code_plausible").passed


class TestRanges:
    def test_ac_in_range(self):
        assert result_for(good_row(), "ac_no_in_range").passed

    @pytest.mark.parametrize("ac_no", [0, 127, 999])
    def test_ac_out_of_range_flagged(self, ac_no):
        row = good_row(ac_no=ac_no, ac_no_file=ac_no)
        assert not result_for(row, "ac_no_in_range").passed

    def test_part_within_ac_uses_zip_counts(self):
        context = validate.CorpusContext(parts_per_ac={1: 154})
        assert result_for(good_row(), "part_no_within_ac", context).passed

    def test_part_beyond_ac_count_flagged(self):
        context = validate.CorpusContext(parts_per_ac={1: 154})
        row = good_row(part_no=200, part_no_file=200)
        assert not result_for(row, "part_no_within_ac", context).passed


class TestCorpusConsistency:
    def test_context_derives_modal_values(self):
        rows = [good_row(), good_row(), good_row(publication_date="2026-09-09")]
        context = validate.build_context(rows)
        assert context.modal_publication_date == "2026-02-10"
        assert context.modal_revision_year == 2026
        assert context.district_by_ac[1] == "কোকৰাঝাৰ"

    def test_outlier_date_is_flagged(self):
        rows = [good_row(), good_row(), good_row(publication_date="2026-09-09")]
        context = validate.build_context(rows)
        assert not result_for(rows[2], "publication_date_matches_corpus", context).passed

    def test_outlier_district_is_flagged(self):
        rows = [good_row(), good_row(), good_row(district="ভুল")]
        context = validate.build_context(rows)
        assert not result_for(rows[2], "district_matches_ac_mode", context).passed

    def test_no_corpus_check_without_a_mode(self):
        """With no consensus there is nothing to compare against; skip, don't guess."""
        names = {r.name for r in validate.check_row(good_row(), validate.CorpusContext())}
        assert "publication_date_matches_corpus" not in names


class TestSelfReport:
    def test_off_template_flagged(self):
        assert not result_for(good_row(template_match=False), "template_match").passed

    def test_low_confidence_flagged(self):
        assert not result_for(good_row(extraction_confidence="LOW"), "confidence_not_low").passed

    def test_medium_confidence_is_acceptable(self):
        assert result_for(good_row(extraction_confidence="MEDIUM"), "confidence_not_low").passed


class TestRequiredText:
    @pytest.mark.parametrize("column", ["ps_name", "district", "ps_address"])
    def test_present_passes(self, column):
        assert result_for(good_row(), f"{column}_present").passed

    @pytest.mark.parametrize("column", ["ps_name", "district", "ps_address"])
    def test_blank_flagged(self, column):
        assert not result_for(good_row(**{column: "   "}), f"{column}_present").passed


class TestSummarize:
    def test_clean_row_needs_no_review(self):
        summary = validate.summarize(validate.check_row(good_row()))
        assert summary["needs_review"] is False
        assert summary["flags"] == ""
        assert summary["checks_passed"] == summary["checks_total"]

    def test_hard_failure_forces_review(self):
        summary = validate.summarize(validate.check_row(good_row(ac_no=2)))
        assert summary["needs_review"] is True
        assert "ac_no_matches_filename" in summary["flags"]

    def test_soft_failure_alone_does_not_force_review(self):
        summary = validate.summarize(validate.check_row(good_row(pin_code="110001")))
        assert summary["needs_review"] is False
        assert "pin_code_plausible" in summary["flags"]

    def test_low_confidence_forces_review_despite_passing_hard_checks(self):
        summary = validate.summarize(validate.check_row(good_row(extraction_confidence="LOW")))
        assert summary["needs_review"] is True


class TestValidateRows:
    def test_populates_qa_columns(self):
        rows = validate.validate_rows([good_row(), good_row(ac_no=2)])
        assert rows[0]["needs_review"] is False
        assert rows[1]["needs_review"] is True


class TestAccuracyReport:
    def test_reports_agreement_rates(self):
        rows = validate.validate_rows([good_row(), good_row(), good_row(ac_no=2)])
        report = validate.accuracy_report(rows)
        assert report["n"] == 3
        assert report["ac_no_agreement"] == pytest.approx(2 / 3, abs=1e-4)
        assert report["part_no_agreement"] == 1.0
        assert report["needs_review"] == 1

    def test_handles_empty_input(self):
        assert validate.accuracy_report([]) == {"n": 0}
