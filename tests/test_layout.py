"""Tests for grid detection.

Uses the real AC1 fixture PDF, so the geometry assertions hold against genuine publisher
output rather than a synthetic page.
"""

import shutil
from pathlib import Path

import pytest
from PIL import Image

from assam_rolls import layout, render

FIXTURE_PDF = (
    Path(__file__).parent
    / "fixtures"
    / ("2026-EROLLGEN-S03-1-FinalRoll-Revision1-ASM-3-WI_INFO.pdf")
)

needs_poppler = pytest.mark.skipif(
    shutil.which("pdfimages") is None, reason="poppler not installed"
)


@pytest.fixture(scope="module")
def page() -> Image.Image:
    return render.extract_page_image(FIXTURE_PDF.read_bytes(), page=render.PAGE_FORM)


@needs_poppler
class TestDetectRules:
    def test_finds_every_stable_rule(self, page):
        found = layout.detect_h_rules(page)
        for expected in layout.STABLE_H_RULES:
            assert any(
                abs(r - expected) <= layout.RULE_TOLERANCE for r in found
            ), f"stable rule {expected} not detected"

    def test_finds_the_elector_table_columns(self, page):
        rules = layout.detect_h_rules(page)
        verticals = layout.detect_v_rules(page, rules[-2], rules[-1])
        for expected in (24, 156, 286, 497, 723, 947, 1162):
            assert any(abs(v - expected) <= layout.RULE_TOLERANCE for v in verticals)

    def test_rules_are_sorted_and_unique(self, page):
        rules = layout.detect_h_rules(page)
        assert rules == sorted(rules)
        assert len(rules) == len(set(rules))


@needs_poppler
class TestBuildGrid:
    def test_produces_every_named_cell(self, page):
        grid = layout.build_grid(page)
        for name in layout.NUMERIC_CELLS + layout.TEXT_CELLS:
            assert name in grid.cells

    def test_cells_are_inside_the_page(self, page):
        grid = layout.build_grid(page)
        for name, box in grid.cells.items():
            assert 0 <= box.x0 < box.x1 <= grid.width, name
            assert 0 <= box.y0 < box.y1 <= grid.height, name

    def test_elector_cells_tile_the_row_without_overlap(self, page):
        """A gap or overlap here would silently read the wrong column."""
        grid = layout.build_grid(page)
        order = [
            "s4_start_serial",
            "s4_end_serial",
            "s4_male",
            "s4_female",
            "s4_third_gender",
            "s4_total",
        ]
        boxes = [grid.cells[n] for n in order]
        for left, right in zip(boxes, boxes[1:]):
            assert left.x1 == right.x0
            assert left.y0 == right.y0 and left.y1 == right.y1

    def test_rejects_a_page_that_is_not_the_template(self):
        blank = Image.new("RGB", layout.EXPECTED_SIZE_HINT, "white")
        with pytest.raises(layout.LayoutError):
            layout.build_grid(blank)

    def test_crop_returns_a_subimage(self, page):
        grid = layout.build_grid(page)
        crop = grid.crop(page, "s4_total")
        box = grid.cells["s4_total"].pad()
        assert crop.size == box.size


class TestBox:
    def test_size(self):
        assert layout.Box(10, 20, 40, 60).size == (30, 40)

    def test_pad_shrinks_on_all_sides(self):
        padded = layout.Box(10, 20, 40, 60).pad(3)
        assert (padded.x0, padded.y0, padded.x1, padded.y1) == (13, 23, 37, 57)


class TestCellClassification:
    def test_numeric_and_text_cells_are_disjoint(self):
        assert not set(layout.NUMERIC_CELLS) & set(layout.TEXT_CELLS)

    def test_all_six_elector_cells_are_numeric(self):
        for name in ("start_serial", "end_serial", "male", "female", "third_gender", "total"):
            assert f"s4_{name}" in layout.NUMERIC_CELLS


@needs_poppler
class TestDebugOverlay:
    def test_returns_a_same_size_rgb_image(self, page):
        overlay = layout.debug_overlay(page, layout.build_grid(page))
        assert overlay.size == page.size
        assert overlay.mode == "RGB"


class TestNonCanonicalPageSize:
    """Pages issued at another size are read at their own resolution, not rescaled.

    Resampling was tried and measured worse in both directions: an interpolating filter
    invented a digit (LANCZOS turned a thin "1" into "4" on 2,598 pages, passing every
    check), and a non-interpolating one discarded detail (NEAREST cost 5.6 points of
    elector agreement). Scaling the grid constants costs neither.
    """

    def test_canonical_pages_have_unit_scale(self, page):
        assert layout.build_grid(page).scale == 1.0

    def test_a_smaller_page_reports_its_scale(self, page):
        small = page.resize((949, 1343))
        grid = layout.build_grid(small)
        assert 0.79 < grid.scale < 0.81

    def test_cells_land_inside_a_smaller_page(self, page):
        """Every cell must be within bounds, or crops raise rather than read."""
        small = page.resize((949, 1343))
        grid = layout.build_grid(small)
        for name, box in grid.cells.items():
            assert 0 <= box.x0 < box.x1 <= small.width, f"{name} x out of bounds"
            assert 0 <= box.y0 < box.y1 <= small.height, f"{name} y out of bounds"

    def test_cells_keep_their_relative_position(self, page):
        """A cell should cover the same fraction of a small page as of a canonical one."""
        big, small = layout.build_grid(page), layout.build_grid(page.resize((949, 1343)))
        for name in ("header_ac", "s2_locality", "s4_total"):
            a, b = big.cells[name], small.cells[name]
            assert abs(a.x0 / big.width - b.x0 / small.width) < 0.02, name
            assert abs(a.y0 / big.height - b.y0 / small.height) < 0.02, name
