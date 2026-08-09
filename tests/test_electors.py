"""The elector stage: geometry, field parsing, and the check that proves completeness.

Every constant here was read off real pages of ``AC1_ASM.zip`` and each test names the
failure it exists to prevent, because all of them were failures first.
"""

from __future__ import annotations

import random

from electors import (
    against,
    bench,
    diagnose,
    escalate,
    evaluate,
    fields,
    grid,
    pages,
    quality,
    replay,
    second_pass,
    timing,
    validate,
)

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

    def test_a_label_missing_its_space_still_matches(self):
        """One character of whitespace cost the relation on 1,588 rows -- 24% of the corpus.

        OCR returns স্বামৰনাম for স্বামীৰ নাম: same letters, no space. A literal substring
        test misses it while the value sits legibly on the same line.
        """
        assert fields.relation_of("স্বামৰনাম' বভজময") == ("বভজময", "husband")
        assert fields.relation_of("স্বামাৰনাম' হুলশা নাজাৰা") == ("হুলশা নাজাৰা", "husband")

    def test_an_unreadable_label_still_yields_the_name(self):
        """The relation name is usually legible even when the word in front of it is not.

        The type is left empty to say the relationship is unknown, rather than dropping a
        value the page plainly holds.
        """
        name, kind = fields.relation_of("গলিত়াৰ মায়: য়ককাই ৰাভা")
        assert name and not kind

    def test_debris_does_not_become_a_relation(self):
        assert fields.relation_of("৷ ক্ৰ ভৰা ক্ম লে *, = |") == ("", "")

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

        def fake_read_part(zip_path, pdf_name, engine=None, capture_lines=False):
            calls.append((pdf_name, capture_lines))
            result = extract.PartResult(1, 7, "ASM", "z.zip", pdf_name, "sha")
            result.electors = [{"ac_no": 1, "part_no": 7, "name": "x"}]
            return result

        monkeypatch.setattr(extract, "read_part", fake_read_part)
        monkeypatch.setattr(cli.render, "read_pdf_bytes", lambda *a: b"bytes")
        monkeypatch.setattr(cli.render, "sha256_bytes", lambda *a: "sha")
        monkeypatch.setattr(cli.ocr, "get_engine", lambda *a, **k: None)

        first = cli._one_part((str(tmp_path / "z.zip"), "part7.pdf", str(tmp_path)))
        second = cli._one_part((str(tmp_path / "z.zip"), "part7.pdf", str(tmp_path)))

        assert calls == [("part7.pdf", False)], "the second call must come from cache"
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

    def test_an_unmeasured_guard_is_a_failure_not_a_pass(self):
        """Skipping a metric absent from either side is how the checks that matter clear
        the gate by being missing. The two with real ground truth are exactly the ones a
        harness is most likely to fail to compute."""
        result = bench.gate_no_damage(
            {"name_present": 0.90},
            {"name_present": 0.90},
            guarded=("name_present", "parts_matching_roll_rate", "male_share"),
        )
        assert not result.passed
        assert "not measured" in result.detail

    def test_guarding_soundness_does_not_punish_removing_wrong_data(self):
        """A fill rate falls when a provably wrong value is correctly cleared.

        Guarding fill would reject the one move that unambiguously improves the data, so the
        gate guards *present and not provably wrong* instead.
        """
        wrong = [{"name": "তন্ডা ৰাভা", "relation_name": "তন্ডা ৰাভা"}]
        cleared = [{"name": "", "relation_name": "তন্ডা ৰাভা"}]
        assert quality.fill_rates(cleared)["name"] < quality.fill_rates(wrong)["name"]
        assert (
            quality.sound_rates(cleared)["name_sound"] == quality.sound_rates(wrong)["name_sound"]
        )

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


class TestReplay:
    """The line cache is only worth having if it reproduces the pipeline exactly."""

    def _box(self):
        return replay.BoxLines(
            page=7,
            section="main",
            col=1,
            row=2,
            lines=[
                "নাম : খাদৰাম ৰাভা",
                "পিতাৰ নাম : গংগাৰাম ৰাভা",
                "ঘৰ নং : 42",
                "বয়স : 35 লিঙ্গ : পুৰুষ",
            ],
            name_second=["নাম : খাদৰাম ৰাভা"],
            epic_raw="S 106 -, HHK3535704",
            serial_raw="106",
        )

    def test_replay_reproduces_the_pipeline_assembler(self):
        """Replay must call the pipeline's own assembler, not a copy of it.

        A harness that reimplements the parser measures the harness. This asserts the two
        paths produce identical values from identical text.
        """
        box = self._box()
        direct = fields.assemble((box.lines, box.name_second), box.epic_raw, box.serial_raw)
        replayed = replay.parse_box(box, serial=1)
        assert replayed["name"] == direct.name
        assert replayed["relation_name"] == direct.relation_name
        assert replayed["age"] == direct.age
        assert replayed["sex"] == direct.sex
        assert replayed["epic_no"] == direct.epic_no

    def test_a_capture_from_older_code_is_refused_not_replayed(self, tmp_path):
        """Replaying text the current pipeline would not produce answers the wrong question."""
        stale = replay.PartLines(
            part=13,
            ac=1,
            capture_version=replay.CAPTURE_VERSION - 1,
            scale=2,
            psm=7,
            lang="asm",
            boxes=[self._box()],
        )
        replay.save(stale, root=tmp_path)
        assert replay.load(13, 1, tmp_path) is None
        assert replay.missing([13], 1, tmp_path) == [13]

    def test_a_current_capture_round_trips(self, tmp_path):
        fresh = replay.PartLines(
            part=13,
            ac=1,
            capture_version=replay.CAPTURE_VERSION,
            scale=2,
            psm=7,
            lang="asm",
            boxes=[self._box()],
        )
        replay.save(fresh, root=tmp_path)
        rows = replay.replay_parts([13], 1, tmp_path)
        assert len(rows) == 1
        assert rows[0]["name"] and rows[0]["box_col"] == 1
        assert replay.cached_parts(tmp_path) == [13]


class TestEscalation:
    """The router decides what the expensive pass reads, so it is measured before it is used."""

    CLEAN = {
        "epic_no": "HHK0001471",
        "name": "খাদৰাম ৰাভা",
        "relation_name": "গংগাৰাম ৰাভা",
        "relation_type": "father",
        "age": 35,
        "sex": "M",
        "flags": "",
    }

    def test_a_clean_row_is_not_escalated(self):
        assert not escalate.needs_escalation(dict(self.CLEAN))

    def test_a_provably_wrong_row_is_escalated(self):
        row = dict(self.CLEAN, name="গংগাৰাম ৰাভা")
        assert "name_equals_relation" in escalate.certain(row)
        assert escalate.needs_escalation(row)

    def test_disagreement_alone_is_enough_to_doubt_a_row(self):
        """Nothing here is known to be wrong -- the two upscales just did not agree."""
        row = dict(self.CLEAN, flags="name_disagreement")
        assert escalate.certain(row) == []
        assert escalate.doubtful(row) == ["name_disagreement"]
        assert escalate.needs_escalation(row)

    def test_one_missing_field_is_not_enough(self):
        """A blank box is the publisher's doing; two missing core fields is a broken read."""
        assert escalate.missing_core(dict(self.CLEAN, age=None)) == []
        assert escalate.missing_core(dict(self.CLEAN, age=None, sex="")) != []

    def test_volume_is_reported_because_it_bounds_the_second_pass_cost(self):
        rows = [dict(self.CLEAN) for _ in range(9)] + [dict(self.CLEAN, name="গংগাৰাম ৰাভা")]
        found = escalate.report(rows)
        assert found.flagged == 1 and found.volume == 0.1

    def test_a_router_that_flags_at_random_scores_no_better_than_chance(self):
        """The check that makes the router falsifiable.

        Scoring precision against the floor detectors could only ever return 100%, because
        they are half the router. This compares agreement on flagged rows against agreement
        on unflagged ones, which a useless router cannot win.
        """
        cheap = [dict(self.CLEAN, flags="name_disagreement" if i % 2 else "") for i in range(20)]
        # The second engine disagrees on every other row, uncorrelated with the flag.
        expensive = [dict(r, name="অন্য নাম" if i % 3 else r["name"]) for i, r in enumerate(cheap)]
        scored = escalate.agreement_against(cheap, expensive)
        assert 0.5 < scored["flagged_agreement_ratio"]["name"] < 1.6

    def test_a_router_that_finds_real_trouble_scores_below_one(self):
        cheap = [dict(self.CLEAN, flags="name_disagreement" if i < 10 else "") for i in range(20)]
        # The second engine disagrees only where the router flagged.
        expensive = [dict(r, name="অন্য নাম" if i < 10 else r["name"]) for i, r in enumerate(cheap)]
        scored = escalate.agreement_against(cheap, expensive)
        assert scored["flagged_agreement_ratio"]["name"] == 0.0


class TestNameConsensus:
    """The second scale must be able to disagree, or it is a check that cannot fail."""

    LINES = [
        "নাম : খাদৰাম ৰাভা",
        "পিতাৰ নাম : গংগাৰাম ৰাভা",
        "ঘৰ নং : 5",
        "বয়স : 35 লিঙ্গ : পুৰুষ",
    ]

    def test_the_second_scale_reading_the_same_band_differently_is_flagged(self):
        """Under the previous version this flag was raised zero times in 10,245 rows.

        The one-line second read was passed through assign_bands, where a single line lands
        in ``house`` -- so the second reading of the *name* was always empty, consensus always
        saw one value, and a full scale-3 pass over every name crop was computed and discarded.
        """
        elector = fields.assemble((self.LINES, ["নাম : লঙ্ষু্ন কঙস্ত্ু"]), "HHK0001471", "106")
        assert "name_disagreement" in elector.flags

    def test_a_one_character_difference_is_noise_and_not_flagged(self):
        """53% of boxes differ between the scales; 83% of those by a character or two.

        Flagging all of them took the escalation router to 56% of rows, which is a re-run
        rather than a triage.
        """
        elector = fields.assemble((self.LINES, ["নাম : খাদৰম ৰাভা"]), "HHK0001471", "106")
        assert elector.flags == []

    def test_agreement_is_not_flagged(self):
        elector = fields.assemble((self.LINES, ["নাম : খাদৰাম ৰাভা"]), "HHK0001471", "106")
        assert elector.flags == []

    def test_no_second_opinion_when_the_name_did_not_come_from_the_top_band(self):
        """Comparing anyway would report the name line disagreeing with the relation line."""
        shifted = self.LINES[1:]
        assigned = fields.assign_bands(shifted)
        assert fields.second_name(shifted, ["নাম : খাদৰাম ৰাভা"], assigned) == ""


class TestDiagnosingWrongValues:
    """An engine that localises only what is absent will never point at what is false."""

    def test_a_wrong_value_is_a_failure_class_the_engine_can_localise(self):
        rows = [
            {
                "name": "গংগাৰাম ৰাভা" if i % 3 == 0 else "খাদৰাম ৰাভা",
                "relation_name": "গংগাৰাম ৰাভা",
                "box_col": i % 3,
                "box_row": i % 10,
            }
            for i in range(90)
        ]
        ranked = {p.failure: p for p in diagnose.priorities(rows)}
        assert "name_equals_relation" in ranked, "the largest error class must be rankable"
        assert ranked["name_equals_relation"].affected == 30

    def test_it_finds_the_slice_a_wrong_value_concentrates_in(self):
        rows = [
            {
                "name": "গংগাৰাম ৰাভা" if i % 3 == 0 else "খাদৰাম ৰাভা",
                "relation_name": "গংগাৰাম ৰাভা",
                "box_col": i % 3,
                "box_row": 0,
            }
            for i in range(90)
        ]
        found = diagnose.associations(rows, features=("box_col",))
        hit = next(a for a in found if a.failure == "name_equals_relation")
        assert hit.value == 0 and hit.rate == 1.0


class TestDerivedFeatures:
    """`diffuse` is a claim about the feature set, not about the data."""

    def _part(self, pages, per_page=3, part=5):
        rows = []
        for page in pages:
            for box_row in range(per_page):
                rows.append({"ac_no": 1, "part_no": part, "page_no": page, "box_row": box_row})
        return rows

    def test_first_and_last_pages_are_distinguished_from_the_middle(self):
        found = diagnose.derive_features(self._part([3, 4, 5]))
        positions = {(r["page_no"], r["page_position"]) for r in found}
        assert positions == {(3, "first"), (4, "middle"), (5, "last")}

    def test_the_bottom_row_of_each_page_is_marked(self):
        found = diagnose.derive_features(self._part([3], per_page=4))
        assert [r["in_last_row"] for r in found] == [False, False, False, True]

    def test_a_cause_invisible_to_the_base_features_is_found_by_the_derived_ones(self):
        """Without page_position this failure looks evenly spread and routes to escalation.

        Routing it there quietly buys a second OCR pass for something a geometry fix solves,
        which is why the thinness of the feature set is a correctness concern and not a
        matter of taste.
        """
        # Four parts, so the first-page slice clears MIN_SUPPORT. A slice below it is not
        # reported at all, which is the intended behaviour: a rate over five rows is noise.
        rows = [r for part in range(1, 5) for r in self._part([1, 2, 3, 4, 5], 10, part)]
        enriched = diagnose.derive_features(rows)
        for row in enriched:
            row["name"] = "" if row["page_position"] == "first" else "খাদৰাম"
        blind = diagnose.associations(enriched, features=("box_col", "box_row"))
        assert not [a for a in blind if a.failure == "no_name"]
        seeing = diagnose.associations(enriched, features=("page_position",))
        hit = next(a for a in seeing if a.failure == "no_name")
        assert hit.value == "first" and hit.rate == 1.0


class TestSwapDetection:
    """A swapped name and relation is populated, plausible, and invisible to the floor."""

    def test_a_relation_without_a_recognised_label_is_doubted(self):
        row = {"name": "হুলশা নাজাৰা", "relation_name": "খাদৰাম ৰাভা", "relation_type": ""}
        assert escalate.certain(row) == [], "nothing here is provably wrong -- that is the point"
        assert escalate.unlabelled_relation(row) == ["relation_type_unknown"]
        assert escalate.needs_escalation(row)

    def test_a_labelled_relation_is_not_doubted(self):
        row = {"name": "খাদৰাম ৰাভা", "relation_name": "গংগাৰাম", "relation_type": "father"}
        assert escalate.unlabelled_relation(row) == []

    def test_an_empty_relation_is_not_a_swap_suspect(self):
        assert escalate.unlabelled_relation({"relation_name": "", "relation_type": ""}) == []


class TestCleaningValues:
    """A trailing matra is usually the word; stripping it truncates almost every surname."""

    def test_real_trailing_matras_survive(self):
        for name in ("খাদৰাম ৰাভা", "শৰ্মা", "বৰুৱা", "গংগাৰাম ৰাভা", "চন্দা মাড"):
            assert fields._clean(name) == name

    def test_a_matra_stranded_after_punctuation_is_debris(self):
        """No Assamese syllable puts a vowel sign after a full stop.

        Left in, it survived every strip, and consensus -- which keeps the longer of two
        readings -- then preferred the dirtier reading over the clean one beside it.
        """
        assert fields._clean("ৰেহমাত বসমাতাৰা .ে") == "ৰেহমাত বসমাতাৰা"

    def test_the_punctuation_class_is_escaped(self):
        """EDGE_PUNCTUATION contains a hyphen, which is a range operator in a character class.

        Unescaped it spanned U+0022 to U+2018 -- every Bengali letter -- so the debris pattern
        matched any trailing mark and truncated শৰ্মা to শ.
        """
        assert fields.ORPHAN_MARK.search("শৰ্মা") is None
        assert fields.ORPHAN_MARK.search("নাম .ে") is not None


class TestDisagreementThreshold:
    """A flag that fires on half the corpus carries no information."""

    def test_noise_is_not_disagreement(self):
        assert not fields.materially_different("ৰেহমাত বসমাতাৰা", "ৰেহমাত বসমাতাৰা")
        assert not fields.materially_different("নলৈেন বসমাতাৰা", "নলৈন বসমাতাৰা")

    def test_genuinely_different_readings_are(self):
        assert fields.materially_different("ল্ষ্মা কস্ষু", "লঙ্ষু্ন কঙস্ত্ু")

    def test_the_threshold_is_the_measured_one(self):
        assert fields.NAME_AGREEMENT == 0.90


class TestGateDirection:
    """An error rate improves by falling; a gate that cannot say so judges the wrong thing."""

    def test_a_falling_error_rate_passes_a_lower_is_better_gate(self):
        result = bench.gate_target("wrong", 0.069, 0.063, minimum=0.005, higher_is_better=False)
        assert result.passed

    def test_a_rising_error_rate_fails_it(self):
        result = bench.gate_target("wrong", 0.063, 0.069, minimum=0.005, higher_is_better=False)
        assert not result.passed

    def test_the_default_is_still_higher_is_better(self):
        assert bench.gate_target("present", 0.80, 0.85).passed
        assert not bench.gate_target("present", 0.85, 0.80).passed


class TestAmbiguousAges:
    """An age of 95 that is really 26 is in range, so no floor detector can see it."""

    def test_a_contaminated_run_is_flagged(self):
        assert fields.age_is_ambiguous("বয়স ' 9526 !লঙ্গ ' মাহলা")

    def test_a_clean_two_digit_age_is_not(self):
        assert not fields.age_is_ambiguous("বয়স ' 46 [লঙ্গ ' মাহলা")

    def test_the_flag_reaches_the_router(self):
        lines = [
            "নাম : খাদৰাম ৰাভা",
            "পিতাৰ নাম : গংগাৰাম",
            "ঘৰ নং : 5",
            "বয়স ' 9526 !লঙ্গ ' মাহলা",
        ]
        elector = fields.assemble((lines, [lines[0]]), "HHK0001471", "106")
        assert "age_ambiguous" in elector.flags
        row = {"flags": ",".join(elector.flags), "relation_type": "father", "relation_name": "x"}
        assert "age_ambiguous" in escalate.doubtful(row)

    def test_the_threshold_follows_the_unambiguous_lines(self):
        """Two-digit runs put 2.3% of ages in the nineties; long runs put 13-15% there
        whichever end is read, so neither end is the age."""
        assert fields.AGE_DIGITS == 2


class TestEscalationCost:
    """Rows flagged overstates the cost: a re-read settles one band, not a whole box."""

    def test_cost_is_counted_in_bands_not_rows(self):
        rows = [
            {
                "name": "খাদৰাম",
                "relation_name": "গংগাৰাম",
                "relation_type": "father",
                "epic_no": "HHK0001471",
                "age": 35,
                "sex": "M",
                "flags": "age_ambiguous" if i % 2 else "",
            }
            for i in range(100)
        ]
        found = escalate.report(rows)
        assert found.volume == 0.5, "half the rows are flagged"
        assert found.by_field == {"age": 50}, "but only one band of each is in question"

    def test_every_reason_the_detectors_can_raise_maps_to_a_band(self):
        """An unmapped reason silently becomes 'box' and overstates the second pass's cost.

        Enumerated from the detectors rather than from a hand-written list, so a reason added
        later without a band fails here instead of quietly inflating the cost estimate.
        """
        broken = {
            "name": "Latin",
            "relation_name": "Latin",
            "relation_type": "",
            "epic_no": "nope",
            "age": 900,
            "sex": "",
            "flags": ",".join(escalate.DOUBT_FLAGS),
        }
        raised = set(escalate.reasons(broken)) | set(quality.problems_with(broken))
        unmapped = sorted(reason for reason in raised if reason not in escalate.REASON_FIELD)
        assert not unmapped, f"reasons with no band: {unmapped}"


class TestDetectorSeparation:
    """A detector can be falsified without ground truth, if not measured."""

    @staticmethod
    def _old(value):
        return value >= 90

    def test_a_detector_that_finds_trouble_concentrates_it(self):
        """Implausible ages sit in the flagged half far more often, but not only there."""
        rows = [
            {
                "age": 95 if (i % 4 == 0 if i % 2 == 0 else i % 20 == 1) else 30,
                "flags": "age_ambiguous" if i % 2 == 0 else "",
            }
            for i in range(200)
        ]
        found = escalate.separation(rows, "age_ambiguous", "age", self._old)
        assert found["concentration"] > 2.0
        assert found["unflagged"]["implausible"] > 0, "a zero base is a different case"

    def test_a_detector_flagging_at_random_scores_about_one(self):
        """The check that makes the whole thing falsifiable.

        Implausible ages are spread evenly across both halves, so a detector that splits the
        corpus in two has found nothing however sensible its reason sounds.
        """
        rows = [
            {"age": 95 if i % 10 < 2 else 30, "flags": "age_ambiguous" if i % 2 else ""}
            for i in range(200)
        ]
        found = escalate.separation(rows, "age_ambiguous", "age", self._old)
        assert 0.8 < found["concentration"] < 1.25

    def test_perfect_separation_is_not_reported_as_failure(self):
        """Dividing by a zero base returned 0.0 -- the score a useless detector gets."""
        rows = [
            {"age": 95 if i % 2 else 30, "flags": "age_ambiguous" if i % 2 else ""}
            for i in range(100)
        ]
        found = escalate.separation(rows, "age_ambiguous", "age", self._old)
        assert found["concentration"] == float("inf")

    def test_no_implausible_values_anywhere_is_no_information(self):
        rows = [{"age": 30, "flags": "age_ambiguous" if i % 2 else ""} for i in range(100)]
        found = escalate.separation(rows, "age_ambiguous", "age", self._old)
        assert found["concentration"] == 1.0


class TestRunTiming:
    """A multi-hour job has to be able to say what it cost."""

    def test_durations_read_the_way_a_long_run_is_discussed(self):
        assert timing.human(5) == "5s"
        assert timing.human(95) == "1m 35s"
        assert timing.human(3600 * 2 + 840) == "2h 14m"
        assert timing.human(-1) == "0s"

    def test_cached_parts_are_kept_out_of_the_per_part_cost(self):
        """Mixing them in makes a resumed run look faster than the pipeline is."""
        clock = timing.RunClock(total=10)
        clock.record(60.0, was_cached=False)
        clock.record(None, was_cached=True)
        assert clock.done == 2 and clock.cached == 1
        assert clock.worked == [60.0]
        assert "1 served from cache" in clock.summary()[0]

    def test_no_estimate_until_each_worker_has_read_a_part(self):
        """A resumed run serves cached parts in a second each.

        Counting those as throughput put the first estimate for a two-hour job at 57 seconds.
        """
        clock = timing.RunClock(total=154, workers=5)
        for _ in range(18):
            clock.record(None, was_cached=True)
        assert clock.eta is None, "eighteen instant parts are not evidence of throughput"
        for _ in range(5):
            clock.record(300.0, was_cached=False)
        assert clock.eta is not None

    def test_the_estimate_comes_from_throughput_not_per_part_cost(self):
        """Per-part cost ignores how many workers are running and what else the machine does."""
        clock = timing.RunClock(total=100, workers=5)
        clock.started -= 600
        for _ in range(10):
            clock.record(300.0, was_cached=False)
        # Ten parts in ten minutes means ninety more take ninety minutes, whatever each cost.
        assert clock.eta is not None
        assert 5300 < clock.eta < 5700

    def test_no_estimate_before_anything_finishes_or_after_everything_has(self):
        assert timing.RunClock(total=10).eta is None
        done = timing.RunClock(total=1)
        done.record(1.0, was_cached=False)
        assert done.eta is None

    def test_progress_names_the_part_and_its_duration(self):
        clock = timing.RunClock(total=154)
        clock.record(61.0, was_cached=False)
        line = clock.progress(7, 873, 61.0, False)
        assert "part 7" in line and "873 electors" in line and "1m 01s" in line

    def test_a_cached_part_says_so_instead_of_reporting_a_duration(self):
        clock = timing.RunClock(total=154)
        clock.record(None, was_cached=True)
        assert "(cached)" in clock.progress(8, 850, None, True)


class TestGatingAgainstARevision:
    """Git already stores every previous parser, so there is no baseline file to go stale."""

    def test_the_three_written_down_sets_stay_separate(self):
        """A regression part has been looked at; quoting it as held-out evidence is a lie."""
        parts = [1, 5, 12, 16, 41, 64]
        found = against.split(parts)
        assert found["in-sample"] == [12, 16]
        assert found["regression"] == [1, 5]
        assert found["out-of-sample"] == [41, 64]

    def test_an_empty_split_is_named_rather_than_dropped(self):
        assert against.split([41, 64])["in-sample"] == []

    def test_a_split_with_nothing_in_it_cannot_pass_its_gate(self):
        """A gate that quietly skips an unmeasured split passes by omission."""
        before, after = against._pair(None, "definitely_wrong")
        assert before == after == 0.0
        gate = bench.gate_target("out-of-sample", before, after, 0.005, higher_is_better=False)
        assert not gate.passed

    def test_the_baseline_parser_is_loaded_not_reimplemented(self):
        """A rewrite that drifts makes the fix look better or worse than it was."""
        module = against.field_module("HEAD")
        assert hasattr(module, "assemble") and hasattr(module, "assign_bands")
        assert module.assemble is not fields.assemble


class TestSerialZone:
    """The serial is a small number in a wide strip -- the shape that defeated the EPIC."""

    def test_the_zone_stays_clear_of_the_cliff(self):
        """Below 40% of the text column the serial is cut off and nothing reads at all.

        Measured on part 70: 35% of the width read 0 of 120 boxes. The chosen fraction has to
        keep a margin above that, which is why the per-part optimum was not taken.
        """
        assert 0.45 <= fields.SERIAL_FRACTION <= 0.65

    def test_the_zone_is_narrower_than_the_text_column(self):
        box = grid.build(H_RULES, V_RULES)[0]
        assert box.text_width == box.text_right - box.left
        zone = int(box.text_width * fields.SERIAL_FRACTION)
        assert zone < box.text_width, "a full-width crop is what made this slow"
        assert zone > box.text_width // 3, "too narrow and the serial falls outside it"


class TestForeignDebris:
    """The roll prints names in Assamese only, so a Latin letter in one is scanner debris."""

    def test_a_speck_beside_a_word_is_removed(self):
        assert fields.strip_foreign("দিপক নাজাখৰা 0") == ("দিপক নাজাখৰা", False)
        assert fields.strip_foreign("1পলাৰ ৰাভা") == ("পলাৰ ৰাভা", False)

    def test_a_digit_between_two_letters_closes_up_and_is_flagged(self):
        """It may have displaced a letter, so the value is repaired but the row stays routable.

        Removing rather than replacing with a space is what makes this a repair: নেম2্ৰা reads
        as নেম্ৰা, not as নেম ্ৰা.
        """
        value, uncertain = fields.strip_foreign("নেম2্ৰা নাজাৰা")
        assert value == "নেম্ৰা নাজাৰা"
        assert uncertain

    def test_separate_words_are_not_joined(self):
        assert fields.strip_foreign("ৰাম 1 কুমাৰ") == ("ৰাম কুমাৰ", False)

    def test_a_clean_name_is_untouched(self):
        assert fields.strip_foreign("খাদৰাম ৰাভা") == ("খাদৰাম ৰাভা", False)

    def test_bengali_digits_count_as_debris_too(self):
        """quality tests names with \\d, which in Python matches any Unicode decimal.

        While this pattern was Latin-only, ২ was provably wrong to the detector and invisible
        to the repair, and 261 rows stayed flagged after the strip meant to clear them.
        """
        cleaned = fields.strip_foreign("ৰোমোছ বসমতাুূৰ ২ ৷")[0]
        assert cleaned == "ৰোমোছ বসমতাুূৰ ৷"
        assert "name_has_latin_or_digits" not in quality.problems_with({"name": cleaned})

    def test_only_the_uncertain_case_reaches_the_router(self):
        """A speck beside a word is repaired with confidence and does not need re-reading."""
        clean = [
            "নাম : দিপক নাজাখৰা 0",
            "পিতাৰ নাম : গংগাৰাম",
            "ঘৰ নং : 5",
            "বয়স : 35 লিঙ্গ : পুৰুষ",
        ]
        assert "name_repaired" not in fields.assemble((clean, []), "HHK0001471", "1").flags
        risky = [
            "নাম : নেম2্ৰা নাজাৰা",
            "পিতাৰ নাম : গংগাৰাম",
            "ঘৰ নং : 5",
            "বয়স : 35 লিঙ্গ : পুৰুষ",
        ]
        assert "name_repaired" in fields.assemble((risky, []), "HHK0001471", "1").flags

    def test_the_house_number_and_epic_are_left_alone(self):
        """Both are digits or Latin by nature; stripping would empty them."""
        lines = [
            "নাম : খাদৰাম ৰাভা",
            "পিতাৰ নাম : গংগাৰাম",
            "ঘৰ নং : 42",
            "বয়স : 35 লিঙ্গ : পুৰুষ",
        ]
        elector = fields.assemble((lines, []), "HHK0001471", "106")
        assert elector.house_no == "42"
        assert elector.epic_no == "HHK0001471"


class TestFuzzyRelationLabel:
    """Enumerating damaged labels does not converge; the shape of the label is stable."""

    def test_the_variants_the_enumeration_missed_now_match(self):
        """1,227 boxes in 21,669 carried a ...নাম label matching none of the eight listed."""
        for line, kind in (
            ('মাতৰ নাম" গিলাশ্বৰা ৰাভা', "mother"),
            ("স্বামূৰ নাম : হুলশা", "husband"),
            ("স্সামাৰ নাম : হুলশা", "husband"),
            ("পৈতাৰ নাম : গংগাৰাম", "father"),
        ):
            found = fields.relation_label(line)
            assert found, line
            assert found[1] == kind, line

    def test_the_name_line_is_never_a_relation(self):
        """The one thing that must never match: it would swap the elector with a relative."""
        for line in ("নাম : খাদৰাম ৰাভা", "ঘৰ নং : 42", "বয়স : 35 লিঙ্গ : পুৰুষ", ""):
            assert fields.relation_label(line) is None, line

    def test_an_ambiguous_relative_yields_a_value_but_no_type(self):
        """1লতাৰ resembles মাতাৰ at 0.60 and is almost certainly a damaged পিতাৰ.

        Publishing a father as a mother is worse than publishing the relationship as unknown,
        and unknown routes the row for a second opinion.
        """
        found = fields.relation_label("1লতাৰ নাম : ৰাম")
        assert found and found[1] == ""

    def test_the_thresholds_keep_their_measured_margins(self):
        """Real variants scored 0.60 and above; ঘৰ, বয়স and a bare name scored 0.33 and below."""
        assert 0.33 < fields.RELATION_MATCH < 0.60
        assert fields.RELATION_TYPE_CONFIDENCE > fields.RELATION_MATCH


class TestCircularAssociations:
    """A feature derived from the failed field explains it by definition, not by evidence."""

    def test_relation_type_does_not_explain_a_missing_relation(self):
        """relation_type is empty because there is no relation. Reported as an 18.2x lift.

        The same family as the serial-gap check that reported zero forever because the serial
        was assigned by a counter: ask what would have to be true for the slice not to light
        up, and if the answer is "nothing", it is not a finding.
        """
        rows = [
            {
                "relation_name": "" if i % 2 else "গংগাৰাম",
                "relation_type": "" if i % 2 else "father",
            }
            for i in range(200)
        ]
        found = diagnose.associations(rows, features=("relation_type",))
        assert not [a for a in found if a.failure == "no_relation"]

    def test_a_real_association_on_the_same_feature_still_reports(self):
        """Only the definitional pair is suppressed, not the feature."""
        rows = [
            {
                "relation_name": "গংগাৰাম",
                "relation_type": "" if i % 2 else "father",
                "name": "" if i % 2 else "খাদৰাম",
            }
            for i in range(200)
        ]
        found = diagnose.associations(rows, features=("relation_type",))
        assert [a for a in found if a.failure == "no_name"]


class TestUnreadableAges:
    """The page plainly holds an age and the cheap pass could not read it."""

    def test_a_two_digit_age_with_one_digit_lost_is_flagged(self):
        """64% of missing ages look like this: বয়স ' 3/ -- one digit came back a symbol."""
        assert fields.age_is_unreadable("বয়স ' 3/ [লঙ্গ ' মাহলা")
        assert fields.age_is_unreadable("বয়স ' 4€ !লঙ্গ ' পৰষ")

    def test_a_readable_age_is_not_flagged(self):
        assert not fields.age_is_unreadable("বয়স ' 46 [লঙ্গ ' মাহলা")

    def test_a_band_with_no_digits_is_a_different_failure(self):
        """No digits means the OCR found nothing to read, not that a value was lost.

        The distinction is what makes escalation worth paying for: one says a better engine
        might recover the value, the other says there is nothing on the crop to recover.
        """
        assert not fields.age_is_unreadable("৷ ক্ল ভকঁনু কুঁমক্র ৷")
        assert not fields.age_is_unreadable("")

    def test_the_flag_routes_the_age_band_and_not_the_box(self):
        lines = ["নাম : খাদৰাম ৰাভা", "পিতাৰ নাম : গংগাৰাম", "ঘৰ নং : 5", "বয়স ' 3/ [লঙ্গ ' মাহলা"]
        elector = fields.assemble((lines, []), "HHK0001471", "1")
        assert "age_unreadable" in elector.flags
        assert escalate.REASON_FIELD["age_unreadable"] == "age"


class TestSecondPass:
    """A second engine is only worth its cost if what it returns can be trusted."""

    def test_it_reads_the_fields_out_of_a_pipe_separated_answer(self):
        reading = second_pass.Reading(
            "61|মৌকথাং|পিতাৰ নাম: বীজেন্দ্ৰ|ঘৰ নং : ১৩|বয়স : 24 লিঙ্গ : পুৰুষ|"
        )
        assert reading.fields() == {"age": 24, "house_no": "13", "sex": "M"}

    def test_the_same_answer_wrapped_in_markup_reads_the_same(self):
        """The model returns bare pipes, <p> wrappers and table rows for one prompt.

        Fields are found by label because position is the thing that moves.
        """
        wrapped = second_pass.Reading("<p>63|নাম : কৌশিলা|ঘৰ নং : ১৪|বয়স : 52 লিঙ্গ : মহিলা</p>")
        assert wrapped.fields() == {"age": 52, "house_no": "14", "sex": "F"}

    def test_a_truncated_answer_is_refused(self):
        """A cap landing mid-value yields a plausible wrong number, which is the whole point."""
        cut = second_pass.Reading("61|মৌকথাং|ঘৰ নং : ১৩|বয়স : 2", tokens=160, capped=True)
        assert not cut.usable and cut.fields() == {}

    def test_a_looping_answer_keeps_its_first_cycle(self):
        text = "|76|সিবিৰানী|ঘৰ নং: ১৭|বয়স: 54|লিঙ্গ: মহিলা|" * 3
        looped = second_pass.Reading(text, tokens=160, capped=True)
        assert looped.usable
        assert looped.fields()["age"] == 54

    def test_an_implausible_age_is_not_accepted_from_the_second_engine_either(self):
        assert "age" not in second_pass.Reading("ঘৰ নং : ১৪|বয়স : 7 লিঙ্গ : পুৰুষ").fields()

    def test_it_fills_gaps(self):
        row = {"age": 35, "house_no": "", "sex": "M", "flags": ""}
        merged = second_pass.merge(row, second_pass.Reading("ঘৰ নং : ১৪|বয়স : 35 লিঙ্গ : পুৰুষ"))
        assert merged["house_no"] == "14"

    def test_a_disputed_age_goes_to_the_second_pass_and_keeps_the_first(self):
        """The crops were read by eye and the second pass won every one: 59 against 92.

        Both values are kept, because the evidence is six crops on one page -- enough to act
        on, not enough to throw away what it replaced.
        """
        row = {"age": 92, "house_no": "", "sex": "M", "flags": ""}
        merged = second_pass.merge(row, second_pass.Reading("ঘৰ নং : ১৪|বয়স : 59 লিঙ্গ : পুৰুষ"))
        assert merged["age"] == 59
        assert merged["age_first_pass"] == 92
        assert "second_pass_overruled" in merged["flags"]

    def test_a_disputed_house_number_is_not_overruled(self):
        """The second pass has only ever filled gaps in house numbers, never contradicted one."""
        row = {"age": 35, "house_no": "42", "sex": "M", "flags": ""}
        merged = second_pass.merge(row, second_pass.Reading("ঘৰ নং : ১৪|বয়স : 35 লিঙ্গ : পুৰুষ"))
        assert merged["house_no"] == "42"
        assert "house_no:42!=14" in merged["second_pass_disagreements"]

    def test_only_rows_missing_something_it_can_supply_are_sent(self):
        """A second read returning what we already had is six seconds spent on nothing."""
        assert not second_pass.wanted({"age": 35, "house_no": "14", "sex": "M"})
        assert second_pass.wanted({"age": None, "house_no": "14", "sex": "M"})
        assert second_pass.wanted({"age": 35, "house_no": "", "sex": "M"})


class TestEngineEvaluation:
    """The only real ground truth here is a crop read by eye."""

    TRUTH = {"a": {"name": "সমে নাৰ্জাৰী", "house": "7", "age": 54, "sex": "F"}}

    def test_it_scores_an_engine_against_the_page(self):
        sample = [
            {
                "key": "a",
                "t_name": "সমে নাজাৰা",
                "t_age": 25,
                "t_house": "",
                "t_sex": "F",
                "surya": (
                    "31|নাম : সমে নাৰ্জাৰী|স্বামীৰ নাম: তিৰেন" "|ঘৰ নং : ৭|বয়স : 54 লিঙ্গ : মহিলা|"
                ),
            }
        ]
        tallies = evaluate.score(self.TRUTH, sample)
        assert tallies["tesseract"].name_exact == 0 and tallies["savitr"].name_exact == 1
        assert tallies["tesseract"].age == 0 and tallies["savitr"].age == 1
        assert tallies["tesseract"].house == 0 and tallies["savitr"].house == 1
        assert tallies["tesseract"].sex == 1 and tallies["savitr"].sex == 1

    def test_a_name_off_by_a_matra_counts_as_nearly_right(self):
        """Distinguishes a different spelling from a different person."""
        sample = [
            {
                "key": "a",
                "t_name": "সমে নাৰ্জাৰি",
                "t_age": None,
                "t_house": "",
                "t_sex": "",
                "surya": "",
            }
        ]
        tallies = evaluate.score(self.TRUTH, sample)
        assert tallies["tesseract"].name_exact == 0
        assert tallies["tesseract"].name_nearly == 1

    def test_fields_are_found_whatever_shape_the_answer_arrives_in(self):
        bare = evaluate.terse_fields("31|সমে নাৰ্জাৰী|ঘৰ নং : ৭|বয়স : 54 লিঙ্গ : মহিলা|")
        wrapped = evaluate.terse_fields(
            "<p>31|নাম : সমে নাৰ্জাৰী|ঘৰ নং : ৭|বয়স : 54 লিঙ্গ : মহিলা</p>"
        )
        assert bare["age"] == wrapped["age"] == 54
        assert bare["house"] == wrapped["house"] == "7"
        assert bare["sex"] == wrapped["sex"] == "F"

    def test_a_relation_line_is_not_taken_for_the_name(self):
        found = evaluate.terse_fields("31|নাম : সমে|স্বামীৰ নাম: তিৰেন|বয়স : 54 লিঙ্গ : মহিলা|")
        assert found["name"] == "সমে"
