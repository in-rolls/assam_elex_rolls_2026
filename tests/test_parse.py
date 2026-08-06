"""Tests for cell-to-field parsing.

The pure helpers carry the subtle logic and are tested directly; the image-driven paths
are exercised against the real AC1 fixture.
"""

import shutil
from pathlib import Path

import pytest
from PIL import Image

from assam_rolls import languages, layout, ocr, parse, render

ASM = languages.profile_for("ASM")

FIXTURE_PDF = (
    Path(__file__).parent / "fixtures" / "2026-EROLLGEN-S03-1-FinalRoll-Revision1-ASM-3-WI_INFO.pdf"
)

needs_tools = pytest.mark.skipif(
    shutil.which("pdfimages") is None or shutil.which("tesseract") is None,
    reason="poppler and tesseract required",
)


@pytest.fixture(scope="module")
def page():
    return render.extract_page_image(FIXTURE_PDF.read_bytes())


class TestIsoDate:
    def test_reformats_a_printed_date(self):
        assert parse.iso_date("01-01-2026") == "2026-01-01"

    def test_finds_a_date_inside_a_label(self):
        assert parse.iso_date("ভিত্তি তাৰিখ 10-02-2026") == "2026-02-10"

    @pytest.mark.parametrize("text", ["", "no date", "2026", None])
    def test_returns_empty_without_a_date(self, text):
        assert parse.iso_date(text) == ""


class TestStripLeadingColon:
    @pytest.mark.parametrize("raw", [": বনগাঁও", ":বনগাঁও", "£ বনগাঁও", "  :  বনগাঁও"])
    def test_removes_colon_however_transcribed(self, raw):
        """Tesseract reads the locality colon as £ often enough to matter."""
        assert parse.strip_leading_colon(raw) == "বনগাঁও"

    def test_leaves_a_clean_value_alone(self):
        assert parse.strip_leading_colon("বনগাঁও") == "বনগাঁও"

    def test_does_not_strip_an_interior_colon(self):
        assert parse.strip_leading_colon("a : b") == "a : b"


class TestSplitNumberedName:
    def test_splits_number_from_name(self):
        assert parse.split_numbered_name("1 - গোসাইগাঁও (সাধাৰণ)") == (
            1,
            "গোসাইগাঁও (সাধাৰণ)",
        )

    def test_handles_multi_digit_numbers(self):
        number, name = parse.split_numbered_name("100 - যোৰহাট")
        assert number == 100 and name == "যোৰহাট"

    def test_handles_an_en_dash(self):
        assert parse.split_numbered_name("14 – যোৰহাট")[0] == 14

    def test_name_without_a_number(self):
        assert parse.split_numbered_name("যোৰহাট") == (None, "যোৰহাট")

    def test_does_not_treat_assamese_numerals_as_the_number(self):
        """৪৩৯ is part of the station's name, not its serial."""
        number, name = parse.split_numbered_name("৪৩৯ নং ঢাপকটা এল পি স্কুল")
        assert number is None
        assert name.startswith("৪৩৯")


class TestSplitReservation:
    @pytest.mark.parametrize(
        "raw,name,code",
        [
            ("গোসাইগাঁও (সাধাৰণ)", "গোসাইগাঁও", "GENERAL"),
            ("কোকৰাঝাৰ (অনুসূচিত জনজাতি)", "কোকৰাঝাৰ", "ST"),
            ("X (অনুসূচিত জাতি)", "X", "SC"),
        ],
    )
    def test_extracts_reservation(self, raw, name, code):
        assert parse.split_reservation(raw, ASM) == (name, code)

    def test_no_bracket_yields_no_reservation(self):
        assert parse.split_reservation("যোৰহাট", ASM) == ("যোৰহাট", "")

    def test_unknown_bracket_is_not_guessed(self):
        assert parse.split_reservation("X (কিবা)", ASM) == ("X", "")


class TestNormalizePsType:
    @pytest.mark.parametrize(
        "raw,code",
        [("পুৰুষ", "MALE"), ("মহিলা", "FEMALE"), ("সাধাৰণ", "GENERAL"), ("সাধাৰন", "GENERAL")],
    )
    def test_maps_to_controlled_vocabulary(self, raw, code):
        assert parse.normalize_ps_type(raw, ASM) == code

    def test_unknown_value_is_blank(self):
        assert parse.normalize_ps_type("???", ASM) == ""


class TestTextRows:
    def test_finds_separated_rows(self):
        image = Image.new("L", (100, 60), "white")
        for y in (10, 40):
            for x in range(5, 95):
                image.putpixel((x, y), 0)
        assert len(parse.text_rows(image)) == 2

    def test_blank_image_has_no_rows(self):
        assert parse.text_rows(Image.new("L", (100, 60), "white")) == []


class TestValueStartX:
    def test_finds_the_boundary_at_a_wide_gap(self):
        image = Image.new("L", (300, 20), "white")
        for x in list(range(0, 50)) + list(range(200, 260)):
            for y in range(5, 15):
                image.putpixel((x, y), 0)
        assert parse.value_start_x(image, (5, 15), default=999) == 200

    def test_falls_back_when_no_wide_gap(self):
        """An empty value leaves only narrow word spacing, so use the default."""
        image = Image.new("L", (300, 20), "white")
        for x in list(range(0, 50)) + list(range(60, 100)):
            for y in range(5, 15):
                image.putpixel((x, y), 0)
        assert parse.value_start_x(image, (5, 15), default=999) == 999

    def test_blank_row_uses_the_default(self):
        blank = Image.new("L", (300, 20), "white")
        assert parse.value_start_x(blank, (5, 15), default=42) == 42


class TestLabelSimilarity:
    def test_identical_labels_score_one(self):
        assert parse.label_similarity("জিলা", "জিলা") == 1.0

    def test_tolerates_the_mangling_tesseract_actually_produces(self):
        """ব্লক is read as বক; the match must survive that but still pick the right label."""
        assert parse.label_similarity("বক", "ব্লক") >= parse.LABEL_MATCH_THRESHOLD

    def test_distinguishes_labels_within_a_block(self):
        """Forgiving is fine; confusing জিলা with ডাকঘৰ is not."""
        seen = "জিলা"
        best = max(ASM.locality_labels, key=lambda lab: parse.label_similarity(seen, lab))
        assert best == "জিলা"

    def test_unrelated_text_scores_low(self):
        assert parse.label_similarity("completely unrelated", "জিলা") < parse.LABEL_MATCH_THRESHOLD


class TestLabelsMatchFields:
    def test_locality_labels_align_with_fields(self):
        assert len(ASM.locality_labels) == len(parse.LOCALITY_FIELDS)

    def test_revision_labels_align_with_fields(self):
        assert len(ASM.revision_labels) == len(parse.REVISION_FIELDS)


class TestLeadingSerial:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("৫6% - ভাৰতী বিদ্যালয়", "ভাৰতী বিদ্যালয়"),
            ("এ4 - লক্ষী ইউনিয়ন হাই স্কুল", "লক্ষী ইউনিয়ন হাই স্কুল"),
            ("€1 - দৰঙী চুক এল পি স্কুল", "দৰঙী চুক এল পি স্কুল"),
        ],
    )
    def test_strips_a_garbled_serial_prefix(self, raw, expected):
        assert parse.LEADING_SERIAL_RE.sub("", raw) == expected

    def test_does_not_eat_a_real_station_number(self):
        """526 and ৬২৫ are part of the name in the source and must survive."""
        for name in ("526 শুড়িপাৰা এল পি স্কুল", "৬২৫ ৰাজেন্দ্ৰপুৰ এল.পি. স্কুল"):
            assert parse.LEADING_SERIAL_RE.sub("", name) == name


@needs_tools
class TestParsePage:
    def test_reads_the_fixture_end_to_end(self, page):
        grid = layout.build_grid(page)
        ref = render.PartRef("AC1.zip", "x.pdf", ac_no=1, part_no=3)
        row, sections = parse.parse_page(page, grid, ref, ocr.get_engine("tesseract"), ASM)

        # Constant across the corpus, so safe to assert exactly.
        assert row["revision_year"] == 2026
        assert row["qualifying_date"] == "2026-01-01"
        assert row["publication_date"] == "2026-02-10"
        assert row["mother_roll_year"] == 2025

        # The arithmetic identity that makes electors self-verifying.
        assert (
            row["electors_male"] + row["electors_female"] + row["electors_third_gender"]
            == row["electors_total"]
        )
        assert row["end_serial"] >= row["start_serial"]
        assert 100000 <= row["pin_code"] <= 999999
        assert row["ps_type"] in ("MALE", "FEMALE", "GENERAL")
        assert row["ac_no_file"] == 1 and row["part_no_file"] == 3
        assert sections and sections[0]["ac_no"] == 1

    def test_provenance_comes_from_the_ref_not_the_page(self, page):
        """ac_no/part_no in the output must be the authoritative filename values."""
        grid = layout.build_grid(page)
        ref = render.PartRef("AC1.zip", "x.pdf", ac_no=1, part_no=3)
        row, _ = parse.parse_page(page, grid, ref, ocr.get_engine("tesseract"), ASM)
        assert row["ac_no_file"] == ref.ac_no
        assert row["part_no_file"] == ref.part_no


class TestReadTrailingNumber:
    """A number printed after a label in the same cell must go to the digit engine.

    Regression: ``part_no`` was read by pulling the integer out of the language model's
    transcription of the whole cell. That rendered 103 as 1093 and, on Assamese pages,
    8 as ৪ -- which the ASCII guard then refused, losing the value. 98.15% -> 100.00%
    over 216 sampled parts.
    """

    def cell(self, ink_columns, width=200, height=20):
        image = Image.new("L", (width, height), 255)
        for x in ink_columns:
            for y in range(4, height - 4):
                image.putpixel((x, y), 0)
        return image

    def test_reads_to_the_right_of_the_widest_right_hand_gap(self):
        seen = {}

        class Engine:
            name = "stub"

            def read_text(self, image, scale=None):
                return "should not be used"

            def read_digits(self, image):
                seen["width"] = image.width
                return "103"

        # label ink on the left, a gap, then the number's ink on the right
        cell = self.cell(list(range(10, 90)) + list(range(130, 180)))
        assert parse.read_trailing_number(cell, Engine()) == "103"
        assert seen["width"] < cell.width, "the label must not reach the digit engine"

    def test_uses_the_digit_engine_not_the_text_engine(self):
        class Engine:
            name = "stub"

            def read_text(self, image, scale=None):
                raise AssertionError("digits must never go through the language model")

            def read_digits(self, image):
                return "7"

        cell = self.cell(list(range(10, 80)) + list(range(140, 170)))
        assert parse.read_trailing_number(cell, Engine()) == "7"

    def test_blank_cell_reads_empty(self):
        class Engine:
            name = "stub"

            def read_text(self, image, scale=None):
                return ""

            def read_digits(self, image):
                return "x"

        assert parse.read_trailing_number(Image.new("L", (200, 20), 255), Engine()) == ""


class TestReadTextRetrying:
    """Text reads retry at other scales, for the same reason digit reads do.

    A row flush against the top of its cell reads at scale 1 and returns empty at the
    default scale 2. Across the corpus that lost ~600 legible values -- 364 block, 162
    post_office, 24 main_town_village and 52 whole section lists.
    """

    class Engine:
        """Returns text only at the scales it is told to succeed at."""

        name = "stub"

        def __init__(self, good_scales, default=2):
            self.good, self.default, self.calls = good_scales, default, []

        def read_text(self, image, scale=None):
            scale = scale or self.default
            self.calls.append(scale)
            return "value" if scale in self.good else ""

        def read_digits(self, image):
            return ""

    def test_retries_when_the_default_scale_reads_nothing(self):
        engine = self.Engine(good_scales={1})
        assert parse.read_text_retrying(engine, Image.new("L", (40, 20), 255)) == "value"
        assert engine.calls[0] == 2, "the default is tried first"
        assert 1 in engine.calls

    def test_a_successful_read_is_never_retried(self):
        engine = self.Engine(good_scales={2})
        assert parse.read_text_retrying(engine, Image.new("L", (40, 20), 255)) == "value"
        assert engine.calls == [2], "a value that already read must not be re-read"

    def test_gives_up_cleanly_when_no_scale_works(self):
        engine = self.Engine(good_scales=set())
        assert parse.read_text_retrying(engine, Image.new("L", (40, 20), 255)) == ""

    def test_blank_and_unreadable_stay_distinct(self):
        """The retry must not turn a genuinely blank cell into an unread one."""
        blank = Image.new("L", (40, 20), 255)
        assert parse.read_value(self.Engine(good_scales=set()), blank) == ""
        inked = Image.new("L", (40, 20), 255)
        for x in range(5, 30):
            for y in range(5, 15):
                inked.putpixel((x, y), 0)
        assert parse.read_value(self.Engine(good_scales=set()), inked) is None


class TestBalanceElectors:
    """The elector table is re-read when its arithmetic fails, and only then.

    This is the one place a *wrong* reading is distinguishable from a right one without a
    human, because the four numbers must sum. Tesseract renders a printed 779 as 7719 at
    every scale above 1 -- deterministically, on 37 parts across eleven unrelated
    constituencies. An empty-read fallback cannot see that; the invariant can.
    """

    def row(self, m, f, t3, total):
        return {
            "electors_male": m,
            "electors_female": f,
            "electors_third_gender": t3,
            "electors_total": total,
        }

    def test_balance_recognises_a_good_row(self):
        assert parse.electors_balance(self.row(400, 379, 0, 779))

    def test_balance_rejects_a_bad_row(self):
        assert not parse.electors_balance(self.row(400, 379, 0, 7719))

    def test_balance_rejects_an_unread_field(self):
        assert not parse.electors_balance(self.row(400, 379, None, 779))

    def test_a_balancing_row_is_never_re_read(self):
        """The safety property: a correct row must not be exposed to a second guess."""
        calls = []

        class Engine:
            name = "stub"

            def read_text(self, image, scale=None):
                return ""

            def read_digits(self, image, scale=None):
                calls.append(scale)
                return "1"

        assert parse.balance_electors(None, None, Engine(), self.row(400, 379, 0, 779)) is None
        assert calls == [], "a balancing row must not trigger any re-read"

    def test_a_candidate_is_accepted_only_when_it_balances(self):
        """A second guess that also fails to balance must not replace the original."""

        class Grid:
            def crop(self, image, name, inset=2):
                return name

        class Engine:
            name = "stub"

            def read_text(self, image, scale=None):
                return ""

            def read_digits(self, image, scale=None):
                # every scale reads a different, still-inconsistent set
                return {
                    "s4_male": "1",
                    "s4_female": "2",
                    "s4_third_gender": "0",
                    "s4_total": "99",
                }.get(image, "0")

        out = parse.balance_electors(Grid(), None, Engine(), self.row(400, 379, 0, 7719))
        assert out is None, "no balancing candidate exists, so nothing may be substituted"
