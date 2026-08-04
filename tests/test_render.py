"""Tests for zip traversal and page-image extraction.

Uses one real source PDF as a fixture (AC1 part 3, the smallest in the corpus) so the
geometry assertions are checked against genuine publisher output rather than a mock.
"""

import shutil
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from assam_rolls import render

FIXTURE_PDF = (
    Path(__file__).parent
    / "fixtures"
    / ("2026-EROLLGEN-S03-1-FinalRoll-Revision1-ASM-3-WI_INFO.pdf")
)

needs_poppler = pytest.mark.skipif(
    shutil.which("pdfimages") is None, reason="poppler not installed"
)


@pytest.fixture
def zip_with_fixture(tmp_path: Path) -> Path:
    """A zip shaped like a real AC bundle, plus one file the parser must ignore."""
    zip_path = tmp_path / "AC1_ASM Roll Info Pages.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.write(FIXTURE_PDF, FIXTURE_PDF.name)
        archive.writestr("readme.txt", "not a pdf")
        archive.writestr("unexpected-name.pdf", "%PDF-1.7 stub")
    return zip_path


class TestIterZipParts:
    def test_finds_the_roll_pdf_and_skips_the_rest(self, zip_with_fixture):
        refs = list(render.iter_zip_parts(zip_with_fixture))
        assert len(refs) == 1
        assert refs[0].ac_no == 1
        assert refs[0].part_no == 3
        assert refs[0].zip_name == zip_with_fixture.name

    def test_reports_unrecognized_pdfs(self, zip_with_fixture):
        """Silently skipping an unexpected PDF would hide missing data."""
        assert render.unrecognized_pdfs(zip_with_fixture) == ["unexpected-name.pdf"]

    def test_sorts_numerically_not_lexicographically(self, tmp_path):
        zip_path = tmp_path / "AC1_ASM Roll Info Pages.zip"
        template = "2026-EROLLGEN-S03-1-FinalRoll-Revision1-ASM-{}-WI_INFO.pdf"
        with zipfile.ZipFile(zip_path, "w") as archive:
            for part in (154, 9, 99, 1):
                archive.writestr(template.format(part), "%PDF-1.7 stub")
        assert [r.part_no for r in render.iter_zip_parts(zip_path)] == [1, 9, 99, 154]


class TestPartRef:
    def test_key_is_zero_padded_and_sortable(self):
        ref = render.PartRef("z.zip", "a.pdf", ac_no=1, part_no=3)
        assert ref.key == "001-0003"

    def test_keys_sort_in_numeric_order(self):
        keys = [render.PartRef("z", "a", ac_no=1, part_no=part).key for part in (154, 9, 99, 1)]
        assert sorted(keys) == ["001-0001", "001-0009", "001-0099", "001-0154"]


class TestSha256:
    def test_is_stable_and_content_dependent(self):
        assert render.sha256_bytes(b"abc") == render.sha256_bytes(b"abc")
        assert render.sha256_bytes(b"abc") != render.sha256_bytes(b"abd")


@needs_poppler
class TestExtractPageImage:
    def test_page_one_matches_native_geometry(self):
        image = render.extract_page_image(FIXTURE_PDF.read_bytes(), page=render.PAGE_FORM)
        assert image.size == render.EXPECTED_PAGE_SIZE

    def test_page_two_is_also_extractable(self):
        image = render.extract_page_image(FIXTURE_PDF.read_bytes(), page=render.PAGE_IMAGERY)
        assert image.size == render.EXPECTED_PAGE_SIZE

    def test_extraction_does_not_resample(self):
        """Byte-exact extraction is the whole point; upsampling would inflate token cost."""
        image = render.extract_page_image(FIXTURE_PDF.read_bytes())
        assert image.size == render.EXPECTED_PAGE_SIZE
        assert image.mode == "RGB"

    def test_raises_on_a_non_pdf(self):
        with pytest.raises(render.RenderError):
            render.extract_page_image(b"not a pdf at all")


class TestFormCrop:
    def test_keeps_full_width_and_trims_height(self):
        image = Image.new("RGB", render.EXPECTED_PAGE_SIZE)
        cropped = render.form_crop(image)
        assert cropped.size == (1187, int(round(1679 * render.FORM_CROP_FRACTION)))

    def test_fraction_is_configurable(self):
        image = Image.new("RGB", (100, 200))
        assert render.form_crop(image, fraction=0.5).size == (100, 100)


class TestEncodePng:
    def test_round_trips(self):
        image = Image.new("RGB", (10, 20), color=(1, 2, 3))
        with Image.open(__import__("io").BytesIO(render.encode_png(image))) as decoded:
            assert decoded.size == (10, 20)


@needs_poppler
class TestRenderZip:
    def test_writes_one_png_per_part(self, zip_with_fixture, tmp_path):
        out = tmp_path / "pages"
        refs = render.render_zip(zip_with_fixture, out)
        assert len(refs) == 1
        assert (out / "001-0003.png").exists()

    def test_is_resumable(self, zip_with_fixture, tmp_path):
        """A second run must not redo work -- statewide this is 28k pages."""
        out = tmp_path / "pages"
        render.render_zip(zip_with_fixture, out)
        target = out / "001-0003.png"
        target.write_bytes(b"sentinel")
        render.render_zip(zip_with_fixture, out)
        assert target.read_bytes() == b"sentinel"

    def test_overwrite_forces_rerender(self, zip_with_fixture, tmp_path):
        out = tmp_path / "pages"
        render.render_zip(zip_with_fixture, out)
        target = out / "001-0003.png"
        target.write_bytes(b"sentinel")
        render.render_zip(zip_with_fixture, out, overwrite=True)
        assert target.read_bytes() != b"sentinel"

    def test_limit_caps_work(self, zip_with_fixture, tmp_path):
        assert render.render_zip(zip_with_fixture, tmp_path / "pages", limit=0) == []
