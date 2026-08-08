"""The elector stage: geometry, field parsing, and the check that proves completeness.

Every constant here was read off real pages of ``AC1_ASM.zip`` and each test names the
failure it exists to prevent, because all of them were failures first.
"""

from __future__ import annotations

import random

from electors import bench, diagnose, fields, grid, pages, quality, validate

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

    def test_sex_survives_a_damaged_scan(self):
        """পুৰুষ comes back as পৰষ or পৰম; মহিলা as মাহলা."""
        assert fields.age_and_sex("বয়স ' 46 [লঙ্গ ' পৰষ") == (46, "M")
        assert fields.age_and_sex("বয়স : 55 লিঙ্গ : মাহলা") == (55, "F")
        assert fields.sex_of("বয়স ' এ4এ4 ললক্ষ ' পৰম") == "M"
        assert fields.sex_of("বয়স : 30 লিঙ্গ : তৃতীয়") == "T"

    def test_sex_scoring_is_symmetric_between_the_words(self):
        """An ordered fragment list skewed the dataset and this is the regression test.

        মহিলা is longer and more distinctive than পুৰুষ, so it survived damage the male word
        did not, and the female fragments were checked first. Against a published 51/49 male
        split the fragment matcher returned 41/59 -- on rows whose age had read perfectly, so
        it was the matcher and not the scan. Scoring all three words and taking the best is
        symmetric by construction.
        """
        male = fields.sex_of("লিঙ্গ ' পৰম")
        female = fields.sex_of("লিঙ্গ ' মহল")
        assert (male, female) == ("M", "F")

    def test_a_line_with_no_sex_word_yields_nothing(self):
        """Scoring must not invent a sex for the name line."""
        assert fields.sex_of("নাম : খাদৰাম ৰাভা") == ""
        assert fields.sex_of("") == ""

    def test_a_garbled_age_line_is_still_found_by_its_sex_label(self):
        """The whole sex bias lived here, not in the sex matcher.

        Five boxes on one page had no age line identified and all five were male: their line
        read ``বৈবলমলস ' 2/ লঙ্গ ' পৰম`` -- বয়স garbled, digits unreadable -- so age and sex
        were both lost. লিঙ্গ survives where বয়স does not.
        """
        for line in (
            "বৈবলমলস ‘*2/ লঙ্গ ‘ পৰম",
            "বৈবযস * ৫48ক8 [লল্গী ‘' পৰম",
            "বৈবলমস ‘*2/ [ল্গ ‘' পৰম",
        ):
            assert fields._is_age_line(line), line
            assert fields.sex_of(line) == "M", line

    def test_a_name_line_is_not_mistaken_for_the_age_line(self):
        """The widened test must not let a name hijack the anchor."""
        assert not fields._is_age_line("নাম * দিপক নাজাখৰা 0")
        assert not fields._is_age_line("পতাৰ নাম‘ কামৰাজ নাজাৰা")

    def test_the_age_line_is_taken_from_the_end(self):
        """It is always last; searching from the front lets a widened match win too early."""
        assigned = fields.assign_bands(
            ["নাম ' লগন বৰুৱা", "পতাৰ নাম' গংগাৰাম", "ছাৰ ন ' 21", "বয়স ' 46 লঙ্গ ' পৰষ"]
        )
        assert "46" in assigned["age"]

    def test_a_one_character_tail_does_not_raise(self):
        """``range(2, 2)`` is empty and ``max()`` raised, killing the whole part.

        Found by a real run dying on it. Every short input must return, not raise.
        """
        for text in ("", "প", "ম", "1", "  ", "-"):
            assert fields.sex_of(text) == ""

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


class TestRollSections:
    """A final roll is the main list plus supplements, in the identical box layout."""

    def test_a_supplement_header_is_recognised(self):
        """Part 8 page 31: যোগ তালিকা 1 (27-12-2025 04-02-2026) -- an addition list."""
        header = "বিধানসভা সমষ্টিৰ নম্বৰ আৰু নাম : 1-গোসাইগাওঁ খণ্ড নং :8 | যোগ তালিকা 1"
        assert pages.section_of(header) == (pages.Section.ADDITION, True)

    def test_a_main_roll_header_is_recognised(self):
        header = "বিধানসভা সমষ্টিৰ নম্বৰ আৰু নাম : 1-গোসাইগাওঁ | অংশৰ নম্বৰ আৰু নাম : 1-বনগাওঁ"
        assert pages.section_of(header) == (pages.Section.MAIN, True)

    def test_an_unreadable_header_is_flagged_not_guessed(self):
        """An unknown supplement wording must surface, not merge silently into the roll."""
        section, recognised = pages.section_of("garbled nonsense")
        assert section is pages.Section.MAIN and not recognised


class TestPageKindsAreSpecific:
    """``unknown`` has to mean something, or it stops being a signal."""

    SUMMARY_H = (38, 200, 400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2400)

    def test_a_summary_with_stray_vertical_marks_is_not_unknown(self):
        """Testing for *no* vertical rules put every summary page into unknown.

        The closing page carries a couple of stray vertical marks, so the seven-part run came
        back with unknown=[25], [30, 32] and so on -- all of them ordinary summaries, drowning
        the flag that exists to surface geometry we do not understand.
        """
        signature = pages.PageSignature(25, pages.PageKind.SUMMARY, self.SUMMARY_H, (100, 900))
        assert signature.kind is pages.PageKind.SUMMARY

    def test_a_two_row_supplement_is_an_elector_page(self):
        """A rule-count floor is a guess about how many electors a page holds.

        At eight horizontal rules it classified an eighteen-elector addition list as a
        summary, and parts 2 and 3 came out 18 and 8 short -- exactly those lists.
        """
        h = (38, 153, 568, 593, 1008)
        v = (78, 854, 1082, 1108, 1133, 1909, 2137, 2189, 2965, 3193)
        assert grid.column_triples(v) and grid.row_bands(h)


class TestResume:
    """A part is durable the moment it is read.

    The first version wrote nothing until all 154 parts finished, so stopping a four-hour run
    discarded four hours. Worse, the commit message claimed the stage was "cached and
    resumable" before any of it existed.
    """

    def payload(self, tmp_path, zip_path="z.zip", pdf="p.pdf"):
        return (str(zip_path), pdf, str(tmp_path))

    def test_a_read_part_is_cached_and_not_read_twice(self, tmp_path, monkeypatch):
        from assam_rolls import cache
        from electors import cli, extract

        calls = []

        def fake_read_part(zip_path, pdf_name, engine=None):
            calls.append(pdf_name)
            result = extract.PartResult(1, 7, "ASM", "z.zip", pdf_name, "sha")
            result.electors = [{"ac_no": 1, "part_no": 7, "name": "x"}]
            return result

        monkeypatch.setattr(extract, "read_part", fake_read_part)
        monkeypatch.setattr(cli.render, "read_pdf_bytes", lambda *a: b"bytes")
        monkeypatch.setattr(cli.render, "sha256_bytes", lambda *a: "sha")
        monkeypatch.setattr(cli.ocr, "get_engine", lambda *a, **k: None)

        first = cli._one_part((str(tmp_path / "z.zip"), "part7.pdf", str(tmp_path)))
        second = cli._one_part((str(tmp_path / "z.zip"), "part7.pdf", str(tmp_path)))

        assert calls == ["part7.pdf"], "the second call must come from cache"
        assert not first["cached"] and second["cached"]
        assert second["electors"] == first["electors"]
        assert cache.read_entry(tmp_path, "part7") is not None

    def test_reissued_source_bytes_invalidate_the_entry(self, tmp_path, monkeypatch):
        """Resumption must never serve data derived from a file that no longer exists."""
        from assam_rolls import cache

        cache.write_entry(tmp_path, "part7", {"part_no": 7}, [], "old-sha")
        entry = cache.read_entry(tmp_path, "part7")
        assert cache.is_fresh(entry, "old-sha")
        assert not cache.is_fresh(entry, "new-sha")


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

    def test_the_serial_check_compares_ocr_against_position(self):
        """The old check could not fail: serial_no is assigned by a counter, so it is 1..N
        by construction. It reported "0 parts with serial gaps" and I quoted that as
        evidence. What can disagree is the serial read off the page.
        """
        rows = self.rows(10)
        for r in rows:
            r["serial_no_ocr"] = r["serial_no"]
        assert validate.reconcile(rows, {})[0].serial_gaps == 0
        rows[5]["serial_no_ocr"] = 99
        assert validate.reconcile(rows, {})[0].serial_gaps == 1

    def test_the_roll_total_is_the_target_not_the_info_page_net(self):
        """Part 1 prints 873 and its closing page says 873; the info page says 850.

        Measuring against the net made a correct extraction look 23 over.
        """
        rows = self.rows(873)
        checks = validate.reconcile(
            rows, {(1, 1): {"total": 850}}, {(1, 1): {"total": 873, "male": 453, "female": 420}}
        )
        assert checks[0].matches_roll and checks[0].roll_shortfall == 0
        summary = validate.summarize(checks, rows)
        assert summary["parts_matching_roll"] == 1
        assert summary["parts_unmeasured"] == []

    def test_a_part_whose_roll_total_is_unreadable_is_excluded_not_guessed(self):
        rows = self.rows(600)
        checks = validate.reconcile(rows, {(1, 1): {"total": 618}})
        assert checks[0].matches_roll is None
        summary = validate.summarize(checks, rows)
        assert summary["parts_measured"] == 0
        assert summary["parts_unmeasured"] == [1]

    def test_the_sex_ratio_is_reported_against_the_published_one(self):
        """A ratio catches what no count can: a matcher that skews the dataset.

        The fragment matcher returned 41% male against a published 51% while the totals
        looked fine, so this is the check that would have caught it.
        """
        rows = self.rows(6, sex="M") + self.rows(4, sex="F")[:4]
        for i, r in enumerate(rows):
            r["serial_no"] = i + 1
        checks = validate.reconcile(rows, {(1, 1): {"total": 10, "male": 5, "female": 5}})
        summary = validate.summarize(checks, rows)
        assert summary["male_share"] == 0.6
        assert summary["published_male_share"] == 0.5

    def test_the_published_gap_is_reported_not_scored(self):
        """The printed roll does not equal the published net, so this is a difference.

        Part 1 prints 870 against a published 850 with no supplement and no blank boxes.
        Treating that as failure would mean deleting real rows.
        """
        rows = self.rows(870)
        summary = validate.summarize(validate.reconcile(rows, {(1, 1): {"total": 850}}), rows)
        assert summary["printed_minus_published_mean"] == 20.0
        assert summary["gap_outliers"] == [], "a single part cannot be an outlier"

    def test_serial_gaps_are_counted_within_each_list(self):
        """Supplements restart at 1, so a part-wide sequence check sees a false gap."""
        rows = self.rows(3)
        extra = self.rows(2, part=1)
        for i, r in enumerate(extra):
            r["serial_no"] = i + 1
            r["roll_section"] = "addition"
        checks = validate.reconcile(rows + extra, {(1, 1): {"total": 5}})
        assert checks[0].serial_gaps == 0
        assert checks[0].supplement_rows == 2


class TestQualityIsNotFillRate:
    """Fill rate says a field is non-empty. These say whether it can possibly be right.

    Every string here came out of a real run; none is invented.
    """

    def test_latin_in_an_assamese_name_is_certainly_wrong(self):
        rows = [{"name": "Kokrajhar midancipalus"}, {"name": "খাদৰাম ৰাভা"}]
        assert quality.definite_errors(rows)["by_kind"]["name_has_latin_or_digits"] == 1

    def test_a_name_equal_to_the_relation_is_certainly_wrong(self):
        """Seen as তন্ডা ৰাভা in both columns -- one band read twice."""
        rows = [{"name": "তন্ডা ৰাভা", "relation_name": "তন্ডা ৰাভা"}]
        assert "name_equals_relation" in quality.definite_errors(rows)["by_kind"]

    def test_a_leaked_label_is_certainly_wrong(self):
        rows = [{"name": "নাম খাদৰাম"}]
        assert "name_contains_label" in quality.definite_errors(rows)["by_kind"]

    def test_a_duplicate_epic_is_certainly_wrong(self):
        """EPICs are unique by construction, so a repeat inside one AC is a misread."""
        rows = [{"epic_no": "HHK0001471"}, {"epic_no": "HHK0001471"}, {"epic_no": "HHK0001472"}]
        assert quality.definite_errors(rows)["duplicate_epics"] == 1

    def test_a_clean_row_trips_nothing(self):
        rows = [
            {
                "name": "খাদৰাম ৰাভা",
                "relation_name": "গংগাৰাম ৰাভা",
                "epic_no": "HHK0001471",
                "age": 46,
            }
        ]
        assert quality.definite_errors(rows)["rows_definitely_wrong"] == 0

    def test_the_floor_and_ceiling_are_reported_separately(self):
        """They bound the error rate from opposite sides and must not be conflated."""
        rows = [
            {"name": "Latin here", "flags": ""},
            {"name": "খাদৰাম ৰাভা", "flags": "name_disagreement"},
        ]
        assert quality.definite_errors(rows)["rate"] == 0.5
        assert quality.disagreement(rows)["name_disagreement"] == 0.5

    def test_the_age_last_digit_histogram_exposes_digit_confusion(self):
        """No per-row check can see this: every age is individually plausible."""
        rows = [{"age": a} for a in (25, 35, 45, 55, 65, 25, 35, 45)]
        dist = quality.distributions(rows)
        assert dist["age_last_digit"] == {5: 8}


class TestTheGate:
    """A fix is accepted only if it clears all four conditions.

    The sex bias was declared fixed twice before its cause was found, because each attempt
    improved the case in front of me. Improving the case in front of you is not evidence.
    """

    def test_a_fix_that_only_works_in_sample_is_rejected(self):
        results = bench.run(
            "epic_present",
            in_sample={"before": 0.83, "after": 0.98},
            out_of_sample={"before": 0.79, "after": 0.80},
            regression_before={},
            regression_after={},
            guarded=(),
        )
        assert not bench.verdict(results)

    def test_a_fix_that_breaks_something_else_is_rejected(self):
        """The gate that was missing: collateral damage to a field nobody was watching."""
        results = bench.run(
            "epic_present",
            in_sample={"before": 0.83, "after": 0.98},
            out_of_sample={"before": 0.79, "after": 0.97},
            regression_before={"parts_matching_roll_rate": 0.60, "name_present": 0.90},
            regression_after={"parts_matching_roll_rate": 0.40, "name_present": 0.90},
            guarded=("parts_matching_roll_rate", "name_present"),
        )
        assert not bench.verdict(results)
        assert any("parts_matching_roll_rate" in r.detail for r in results)

    def test_a_fix_clearing_everything_is_accepted(self):
        results = bench.run(
            "epic_present",
            in_sample={"before": 0.826, "after": 0.981},
            out_of_sample={"before": 0.792, "after": 0.977},
            regression_before={"name_present": 0.90},
            regression_after={"name_present": 0.90},
            guarded=("name_present",),
            seconds=(800.0, 780.0),
        )
        assert bench.verdict(results)

    def test_the_splits_are_disjoint(self):
        """An out-of-sample set that overlaps the others is not out of sample."""
        parts = list(range(1, 155))
        splits = bench.split_parts(parts)
        assert not set(splits["validate"]) & set(splits["diagnose"])
        assert not set(splits["validate"]) & set(splits["regression"])

    def test_the_held_out_split_is_reproducible(self):
        parts = list(range(1, 155))
        assert bench.validate_parts(parts) == bench.validate_parts(parts)

    def test_a_ratio_is_scored_by_closeness_not_by_size(self):
        """Overshooting the target is not an improvement."""
        under = bench.metrics_from({}, {"male_share": 0.45, "roll_male_share": 0.508})
        near = bench.metrics_from({}, {"male_share": 0.505, "roll_male_share": 0.508})
        over = bench.metrics_from({}, {"male_share": 0.60, "roll_male_share": 0.508})
        assert near["male_share"] > under["male_share"]
        assert near["male_share"] > over["male_share"]


class TestTheProposalEngine:
    """Root causes are co-occurrences. Found by eye they neither repeat nor scale."""

    def rows(self, failing_column):
        out = []
        for col in (0, 1, 2):
            for i in range(60):
                lost = col == failing_column and i < 30
                out.append(
                    {
                        "box_col": col,
                        "box_row": i % 10,
                        "roll_section": "main",
                        "sex": "" if lost else "M",
                        "age": None if lost else 40,
                        "epic_no": "HHK0001471",
                        "name": "ৰাভা",
                        "relation_name": "গংগাৰাম",
                        "house_no": "2",
                        "flags": "",
                    }
                )
        return out

    def test_it_finds_a_failure_concentrated_in_one_column(self):
        found = diagnose.associations(self.rows(2), min_support=10)
        assert found and found[0].feature == "box_col" and found[0].value == 2
        assert found[0].lift > 2

    def test_it_proposes_the_crop_when_the_signature_is_spatial(self):
        found = diagnose.associations(self.rows(2), min_support=10)
        assert any("crop" in p for p in diagnose.proposals(found))

    def test_evenly_spread_failures_produce_nothing(self):
        """It must not invent a cause when there is no pattern.

        The first version of this test used ``i % 5`` for failures and ``i % 10`` for the row,
        which puts every failure in rows 0 and 5 -- a real association. The engine found it and
        the test was wrong. Randomised so the null really is null.
        """
        rng = random.Random(3)
        rows = [
            {
                "box_col": rng.randrange(3),
                "box_row": rng.randrange(10),
                "sex": "" if rng.random() < 0.2 else "M",
                "flags": "",
            }
            for _ in range(3000)
        ]
        assert diagnose.associations(rows, min_support=100) == []

    def test_empty_and_garbled_reads_are_separated(self):
        """They want opposite fixes and are identical in a fill rate."""
        shapes = dict(diagnose.raw_read_shapes(["", "", "", "S 106 -, HHK3535/704", "", "HH"]))
        assert shapes["empty"] == 4 and shapes["garbled"] == 1
