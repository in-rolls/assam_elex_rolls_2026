"""Tests for dictionary canonicalisation.

The cases here are the real pairs from the corpus that drove the design: three merges
that a similarity-only rule got wrong, and the systematic error that clustering must
*not* pretend to fix.
"""

import pytest

from assam_rolls import dictionary as dc


class TestMergeable:
    @pytest.mark.parametrize(
        "a,b",
        [
            ("কোকৰাব্বাৰ", "কোকৰাব্মাৰ"),  # 0.900
            ("কোকৰাব্াৰ", "কোকৰাব্মাৰ"),  # 0.947
            ("ফকিৰগঞ্জা", "ফকিৰগঞ্জ"),  # 0.941
        ],
    )
    def test_merges_ocr_variants_of_one_name(self, a, b):
        assert dc.mergeable(a, b)

    def test_a_near_miss_is_not_merged_by_similarity_alone(self):
        """কোকৰাবাৰ scores 0.889, just under the bar, so clustering leaves it alone.

        It is still canonicalised for district -- but through ``collapse_to_one``, which
        settles a constant-per-AC field by vote rather than by pairwise similarity. The
        two mechanisms are separate on purpose, and this pins which one applies.
        """
        assert not dc.mergeable("কোকৰাবাৰ", "কোকৰাব্মাৰ")
        values = ["কোকৰাব্মাৰ"] * 142 + ["কোকৰাবাৰ"]
        d = dc.build_field_dictionary(values, "district", 1, collapse_to_one=True)
        assert d.apply("কোকৰাবাৰ") == "কোকৰাব্মাৰ"

    def test_keeps_different_blocks_apart(self):
        """Gossaigaon and Kachugaon dev blocks share a long suffix (0.85 similarity)."""
        assert not dc.mergeable("গোসাইগাওঁ উন্নয়ন খণ্ড (2-1)", "কচুগাওঁ উন্নয়ন খণ্ড (2-1)")

    def test_keeps_jorhat_and_madhya_jorhat_apart(self):
        assert not dc.mergeable("যোৰহাট (অংশ-২)", "মধ্য যোৰহাট (অংশ-২)")

    def test_digit_guard_separates_part_one_from_part_two(self):
        """These score 0.96 on similarity alone -- only the digits tell them apart."""
        a, b = "উত্তৰ পশ্চিম যোৰহাট (অংশ-১)", "উত্তৰ পশ্চিম যোৰহাট (অংশ-২)"
        assert dc.similarity(a, b) > dc.SIMILARITY_THRESHOLD
        assert not dc.mergeable(a, b)

    def test_identical_values_merge(self):
        assert dc.mergeable("যোৰহাট", "যোৰহাট")


class TestDigitSignature:
    def test_folds_assamese_and_latin_digits_together(self):
        assert dc.digit_signature("(১-1)") == dc.digit_signature("(1-1)")

    def test_distinguishes_different_numbers(self):
        assert dc.digit_signature("(অংশ-১)") != dc.digit_signature("(অংশ-২)")

    def test_empty_when_no_digits(self):
        assert dc.digit_signature("যোৰহাট") == ""


class TestClusterValues:
    def test_groups_variants_around_the_dominant_reading(self):
        values = ["কোকৰাব্মাৰ"] * 142 + ["কোকৰাব্বাৰ"] * 8 + ["কোকৰাব্াৰ"] * 3
        clusters = dc.cluster_values(values)
        assert len(clusters) == 1
        assert clusters[0].canonical == "কোকৰাব্মাৰ"
        assert clusters[0].total == 153

    def test_keeps_genuinely_distinct_values_separate(self):
        values = ["যোৰহাট (অংশ-২)"] * 10 + ["মধ্য যোৰহাট (অংশ-২)"] * 20
        assert len(dc.cluster_values(values)) == 2

    def test_ignores_blanks(self):
        assert dc.cluster_values(["", "  ", None]) == []


class TestBuildFieldDictionary:
    def test_collapses_a_constant_field_to_one_value(self):
        values = ["যোৰহাট"] * 50 + ["যোৱহাট"] * 3
        d = dc.build_field_dictionary(values, "district", 100, collapse_to_one=True)
        assert d.apply("যোৱহাট") == "যোৰহাট"
        assert not d.contested

    def test_marks_a_close_call_contested_instead_of_guessing(self):
        """Forcing a winner from a near-tie would fabricate agreement."""
        values = ["ধুবুৰী"] * 10 + ["শোণিতপুৰ"] * 9
        d = dc.build_field_dictionary(values, "district", 1, collapse_to_one=True)
        assert d.contested
        assert d.apply("শোণিতপুৰ") == "শোণিতপুৰ"  # unchanged

    def test_repeated_field_keeps_its_distinct_values(self):
        values = ["যোৰহাট (অংশ-২)"] * 10 + ["মধ্য যোৰহাট (অংশ-২)"] * 20
        d = dc.build_field_dictionary(values, "block", 100, collapse_to_one=False)
        assert d.apply("যোৰহাট (অংশ-২)") == "যোৰহাট (অংশ-২)"
        assert d.apply("মধ্য যোৰহাট (অংশ-২)") == "মধ্য যোৰহাট (অংশ-২)"


class TestSystematicErrorSurvives:
    def test_clustering_cannot_fix_an_error_made_on_every_page(self):
        """The central limitation, asserted so it cannot be forgotten.

        Tesseract misreads কোকৰাঝাৰ identically on all 154 AC1 pages. Voting therefore
        elects the wrong value; only a second engine can catch it.
        """
        values = ["কোকৰাব্মাৰ"] * 154
        d = dc.build_field_dictionary(values, "district", 1, collapse_to_one=True)
        assert d.apply("কোকৰাব্মাৰ") == "কোকৰাব্মাৰ"
        assert d.apply("কোকৰাব্মাৰ") != "কোকৰাঝাৰ"


class TestApplyToRows:
    def test_writes_canonical_alongside_raw(self):
        rows = [{"ac_no_file": 1, "district": "কোকৰাব্মাৰ"} for _ in range(20)]
        rows += [{"ac_no_file": 1, "district": "কোকৰাব্বাৰ"}]
        out = dc.apply_to_rows(rows)
        assert out[-1]["district"] == "কোকৰাব্বাৰ"  # raw preserved
        assert out[-1]["district_canonical"] == "কোকৰাব্মাৰ"

    def test_scopes_are_independent(self):
        rows = [{"ac_no_file": 1, "district": "কোকৰাঝাৰ"} for _ in range(10)]
        rows += [{"ac_no_file": 10, "district": "ধুবুৰী"} for _ in range(10)]
        out = dc.apply_to_rows(rows)
        assert out[0]["district_canonical"] == "কোকৰাঝাৰ"
        assert out[-1]["district_canonical"] == "ধুবুৰী"

    def test_handles_an_empty_corpus(self):
        assert dc.apply_to_rows([]) == []
