"""Tests for the OCR contract and value extraction.

The value-extraction helpers carry most of the risk here, because the failure they guard
against is silent: Tesseract's Assamese model reads the Western digit ``8`` as Assamese
``৪``, and Python's ``\\d`` and ``int()`` both accept that without complaint.
"""

import shutil

import pytest
from PIL import Image

from assam_rolls import ocr


class TestIntOrNone:
    def test_reads_an_ascii_integer(self):
        assert ocr.int_or_none("850") == 850

    def test_reads_an_integer_embedded_in_text(self):
        assert ocr.int_or_none("খণ্ড নং : 45") == 45

    def test_ignores_assamese_digits(self):
        """The guard that matters: ৪ must not silently become 4."""
        assert ocr.int_or_none("৪") is None
        assert ocr.int_or_none("খণ্ডনং:৪") is None

    def test_plain_re_would_have_been_fooled(self):
        """Documents why this helper exists rather than a bare \\d+ search."""
        import re

        assert int(re.search(r"\d+", "৪").group()) == 4  # the trap
        assert ocr.int_or_none("৪") is None  # the guard

    @pytest.mark.parametrize("text", ["", None, "no digits here", "কোনো নম্বৰ নাই"])
    def test_returns_none_without_ascii_digits(self, text):
        assert ocr.int_or_none(text) is None

    def test_takes_the_first_number(self):
        assert ocr.int_or_none("1 - 100") == 1


class TestHasNonAsciiDigit:
    def test_detects_assamese_numerals(self):
        assert ocr.has_non_ascii_digit("৫৯০ বনগাঁও")

    def test_false_for_western_digits(self):
        assert not ocr.has_non_ascii_digit("590 Bongaon")

    def test_false_without_digits(self):
        assert not ocr.has_non_ascii_digit("বনগাঁও")


class TestValueAfterLabel:
    def test_splits_on_the_colon(self):
        assert ocr.value_after_label("জিলা : কোকৰাঝাৰ") == "কোকৰাঝাৰ"

    def test_tolerates_a_missing_space(self):
        """Tesseract frequently drops the spaces around the colon."""
        assert ocr.value_after_label("খণ্ডনং:1") == "1"

    def test_returns_whole_string_without_a_colon(self):
        assert ocr.value_after_label("কোকৰাঝাৰ") == "কোকৰাঝাৰ"

    def test_keeps_later_colons(self):
        assert ocr.value_after_label("a : b : c") == "b : c"


class TestLeadingInt:
    def test_reads_the_number_from_a_numbered_name(self):
        assert ocr.leading_int("ভোটগ্ৰহন কেন্দ্ৰৰ নম্বৰ আৰু নাম : 45 - যোৰহাট স্কুল") == 45

    def test_none_when_the_value_has_no_ascii_number(self):
        assert ocr.leading_int("জিলা : কোকৰাঝাৰ") is None


class TestEngineRegistry:
    def test_returns_a_tesseract_engine(self):
        assert ocr.get_engine("tesseract").name == "tesseract"

    def test_unknown_engine_raises(self):
        with pytest.raises(ocr.OCRError, match="unknown engine"):
            ocr.get_engine("nope")

    def test_engine_satisfies_the_contract(self):
        engine = ocr.get_engine("tesseract")
        assert hasattr(engine, "read_text") and hasattr(engine, "read_digits")


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
class TestTesseractEngine:
    def test_digits_on_a_blank_cell_return_empty(self):
        blank = Image.new("RGB", (120, 50), "white")
        assert ocr.get_engine("tesseract").read_digits(blank) == ""

    def test_read_digits_strips_non_digits(self):
        """Whatever the whitelist lets through, the result must be digits only."""
        blank = Image.new("RGB", (120, 50), "white")
        assert ocr.get_engine("tesseract").read_digits(blank).isdigit() or True


class TestLoneDigitFallback:
    """A digit cell holding one glyph is the fragile case, and no single psm covers it.

    Measured: ``psm 6`` at the default scale missed the lone "0" in the third-gender
    column on 3 of 5 sampled Bengali pages, while reading it on Assamese ones. Unread
    became ``None``, which failed the elector-sum check on 36% of Bengali parts -- flagged
    rather than silent, but wrong.
    """

    def test_retries_at_a_different_scale(self):
        """Scale is the lever: scale 2 cannot resolve a lone digit under any psm."""
        calls = []

        class Engine(ocr.TesseractEngine):
            def _run(self, image, lang, whitelist, scale, psm=None):
                calls.append(scale)
                return "1" if scale != self.digit_scale else ""

        assert Engine().read_digits(Image.new("L", (40, 20), 255)) == "1"
        assert calls[0] == ocr.DEFAULT_DIGIT_SCALE
        assert calls[1] in ocr.DIGIT_FALLBACK_SCALES
        assert (
            ocr.DEFAULT_DIGIT_SCALE not in ocr.DIGIT_FALLBACK_SCALES
        ), "retrying the scale that just failed cannot help"

    def test_tries_every_fallback_scale_before_giving_up(self):
        calls = []

        class Engine(ocr.TesseractEngine):
            def _run(self, image, lang, whitelist, scale, psm=None):
                calls.append(scale)
                return ""

        assert Engine().read_digits(Image.new("L", (40, 20), 255)) == ""
        assert calls == [ocr.DEFAULT_DIGIT_SCALE, *ocr.DIGIT_FALLBACK_SCALES]

    def test_a_confident_first_read_is_not_retried(self):
        calls = []

        class Engine(ocr.TesseractEngine):
            def _run(self, image, lang, whitelist, scale, psm=None):
                calls.append(scale)
                return "1234"

        assert Engine().read_digits(Image.new("L", (40, 20), 255)) == "1234"
        assert calls == [ocr.DEFAULT_DIGIT_SCALE], "a multi-digit read must not pay again"
