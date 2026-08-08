"""The elector stage: geometry, field parsing, and the check that proves completeness.

Every constant here was read off real pages of ``AC1_ASM.zip`` and each test names the
failure it exists to prevent, because all of them were failures first.
"""

from __future__ import annotations

from electors import fields, grid, pages, validate

#: One real elector page from part 1, at 400 dpi. Vertical rules come in clusters because
#: adjacent boxes share a border; the extras at 749, 1189 ... are the internal rule some
#: boxes carry under the EPIC line.
V_RULES = (78, 854, 1082, 1108, 1133, 1909, 2137, 2163, 2189, 2965, 3193, 3219)
H_RULES = (
    38,
    153,
    568,
    593,
    1008,
    1033,
    1451,
    1476,
    1891,
    1916,
    2331,
    2356,
    2771,
    2796,
    3214,
    3236,
    3654,
    3679,
    4094,
    4119,
    4534,
)
H_RULES_WITH_INTERNALS = (
    38,
    153,
    568,
    593,
    749,
    1008,
    1033,
    1189,
    1451,
    1476,
    1629,
    1891,
    1916,
    2331,
    2356,
    2771,
    2796,
    2951,
    3214,
    3236,
    3392,
    3654,
    3679,
    4094,
    4119,
    4273,
    4534,
)


class TestGridGeometry:
    def test_columns_come_from_shared_borders(self):
        """Adjacent boxes share a border, so three columns give seven clusters, not nine rules.

        Reading the rules as consecutive triples finds nothing at all.
        """
        columns = grid.column_triples(V_RULES)
        assert len(columns) == grid.COLUMNS
        for left, divider, right in columns:
            assert left < divider < right
            assert right - left == 1004, "every box is the same width"

    def test_ten_rows_on_a_full_page(self):
        assert len(grid.row_bands(H_RULES)) == grid.ROWS

    def test_internal_rules_do_not_break_the_rows(self):
        """Where most boxes carry the internal rule, its spans outnumber the real row height.

        Choosing the row height by frequency picks 156 and returns six rows for a page
        holding thirty electors; choosing it by the page area it explains picks 415.
        """
        assert len(grid.row_bands(H_RULES_WITH_INTERNALS)) == grid.ROWS

    def test_rows_are_separated_by_a_real_gutter(self):
        """Without this the bottom rule of one row pairs with the bottom of the next."""
        rows = grid.row_bands(H_RULES)
        for (_, bottom), (top, _) in zip(rows, rows[1:]):
            assert top > bottom

    def test_a_full_page_yields_thirty_boxes(self):
        assert len(grid.build(H_RULES, V_RULES)) == grid.COLUMNS * grid.ROWS

    def test_the_photo_column_is_excluded_from_the_text_crop(self):
        """OCR of a whole box drops the name: ফটো উপলব্ধ merges into the text lines."""
        box = grid.build(H_RULES, V_RULES)[0]
        assert box.text_right < box.right


class TestPageClassification:
    def test_an_elector_page_is_recognised_by_its_columns(self):
        signature = pages.PageSignature(3, pages.PageKind.ELECTOR, H_RULES, V_RULES)
        assert signature.is_elector

    def test_unknown_pages_are_reported_not_parsed(self):
        signatures = [
            pages.PageSignature(4, pages.PageKind.ELECTOR, H_RULES, V_RULES),
            pages.PageSignature(5, pages.PageKind.UNKNOWN, (), ()),
        ]
        assert pages.unknown_pages(signatures) == [5]
        assert [s.number for s in pages.elector_pages(signatures)] == [4]


class TestFieldParsing:
    def test_a_trailing_matra_is_part_of_the_name(self):
        """Stripping it truncated ৰাভা to ৰাভ -- almost every surname on the page."""
        assert fields._clean("খাদৰাম ৰাভা |") == "খাদৰাম ৰাভা"

    def test_a_leading_matra_is_scanner_debris(self):
        assert fields._clean("ু ' বাসন্তা ৰাভা") == "বাসন্তা ৰাভা"

    def test_relation_type_comes_from_the_label(self):
        assert fields.relation_of("পিতাৰ নাম : গংগাৰাম ৰাভা") == ("গংগাৰাম ৰাভা", "father")
        assert fields.relation_of("স্বামীৰ নাম : খাদৰাম ৰাভা") == ("খাদৰাম ৰাভা", "husband")

    def test_sex_is_matched_as_a_fragment(self):
        """পুৰুষ comes back as পৰষ; a whole-word table misses every damaged scan."""
        assert fields.age_and_sex("বয়স ' 46 [লঙ্গ ' পৰষ") == (46, "M")
        assert fields.age_and_sex("বয়স : 55 লিঙ্গ : মাহলা") == (55, "F")

    def test_an_implausible_age_is_not_recorded(self):
        assert fields.age_and_sex("বয়স : 7 লিঙ্গ : পুৰুষ")[0] is None

    def test_a_doubled_digit_still_yields_an_age(self):
        """The Assamese model doubles digits it is unsure of: 55 comes back as 525."""
        assert fields.age_from("বয়স ' 525 !লঙ্গ ' মাহলা") == 52
        assert fields.age_from("বয়স : 4 6") == 46

    def test_an_unreadable_box_is_still_a_row(self):
        """Ink decides emptiness, not whether any field parsed.

        Conflating "the publisher left this blank" with "OCR failed here" drops the second
        silently, and every part then comes up short of its published total.
        """
        assert fields.Elector(flags=["unreadable"]).is_empty is True

    def test_a_box_with_only_a_relation_is_not_treated_as_blank(self):
        """Testing only EPIC, name and age left the first run nine electors short."""
        assert not fields.Elector(relation_name="গংগাৰাম ৰাভা").is_empty
        assert fields.Elector().is_empty

    def test_bands_are_anchored_on_the_age_line(self):
        """The house line scans worst of all, so it cannot be found by its own label."""
        assigned = fields.assign_bands(
            [
                "নাম ' খাদৰাম ৰাভা",
                "পতাৰ নাম' গংগাৰাম ৰাভা",
                "ছাৰ ন ' 21",
                "বয়স ' 46 [লঙ্গ ' পৰষ",
            ]
        )
        assert "46" in assigned["age"]
        assert assigned["house"] == "ছাৰ ন ' 21"
        assert "গংগাৰাম" in assigned["relation"]

    def test_house_number_falls_back_to_its_digits(self):
        assert fields.house_number("ছাৰ ন ' 21") == "21"

    def test_scales_that_agree_are_trusted_and_disagreement_is_flagged(self):
        assert fields.consensus(["ৰাভা", "ৰাভা"]) == ("ৰাভা", True)
        value, agreed = fields.consensus(["ৰাভ", "ৰাভা"])
        assert value == "ৰাভা" and not agreed


class TestReconciliation:
    """The check that makes this stage trustworthy: the source published the answer."""

    def rows(self, n, ac=1, part=1, sex="M"):
        return [
            {"ac_no": ac, "part_no": part, "serial_no": i + 1, "sex": sex, "name": "x"}
            for i in range(n)
        ]

    def test_a_part_that_matches_its_published_total(self):
        checks = validate.reconcile(self.rows(850), {(1, 1): {"total": 850, "male": 850}})
        assert checks[0].counts_match and checks[0].shortfall == 0

    def test_a_short_part_is_caught(self):
        """The failure this whole design exists to detect: a page that did not resolve."""
        checks = validate.reconcile(self.rows(820), {(1, 1): {"total": 850}})
        assert not checks[0].counts_match
        assert checks[0].shortfall == -30

    def test_a_serial_gap_is_caught_even_when_the_count_is_right(self):
        rows = self.rows(10)
        rows[5]["serial_no"] = 99
        checks = validate.reconcile(rows, {(1, 1): {"total": 10}})
        assert checks[0].counts_match and checks[0].serial_gaps

    def test_summary_reports_the_rate_over_checkable_parts_only(self):
        rows = self.rows(5) + self.rows(5, part=2)
        checks = validate.reconcile(rows, {(1, 1): {"total": 5}})
        summary = validate.summarize(checks, rows)
        assert summary["parts"] == 2
        assert summary["parts_with_published_total"] == 1
        assert summary["parts_exact_rate"] == 1.0
