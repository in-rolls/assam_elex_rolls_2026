"""A part resumed from the bucket has its words but not its images.

Composites are never uploaded -- a terabyte for the state, and cheap to rebuild -- so this is the
normal state of every part on a machine that restarted. The two questions that decide what happens
to it, ``stage1.done`` and ``stage2.ready``, have each been wrong about it in a different and
expensive direction, so both are pinned here against all three states.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from electors import stage1, stage2

PLACEMENTS = {
    "composite000.png": [{"left": 0, "top": 0, "right": 10, "bottom": 10, "page": 3, "box": 0}],
    "composite001.png": [{"left": 0, "top": 0, "right": 10, "bottom": 10, "page": 3, "box": 1}],
}


def _part(tmp_path: Path, images: bool, words: bool) -> Path:
    part = tmp_path / "part0001"
    part.mkdir()
    (part / "manifest.jsonl").write_text("{}\n", encoding="utf-8")
    (part / "placements.json").write_text(json.dumps(PLACEMENTS), encoding="utf-8")
    (part / "side.json").write_text(
        json.dumps({"stage1_version": stage1.STAGE1_VERSION, "pages": {}}), encoding="utf-8"
    )
    for name in PLACEMENTS:
        if images:
            (part / name).write_bytes(b"not really a png")
        if words:
            (part / f"{Path(name).stem}.words.json").write_text("[]", encoding="utf-8")
    return part


def test_images_present_is_prepared(tmp_path: Path) -> None:
    _part(tmp_path, images=True, words=False)
    assert stage1.done(tmp_path, 1)
    assert stage2.ready(tmp_path / "part0001")


def test_words_without_images_is_prepared(tmp_path: Path) -> None:
    """The resumed-from-bucket case. Calling this unprepared re-renders the whole state."""
    _part(tmp_path, images=False, words=True)
    assert stage1.done(tmp_path, 1)
    assert stage2.ready(tmp_path / "part0001")


def test_neither_is_not_prepared(tmp_path: Path) -> None:
    """AC101's failure: metadata alone looked finished, and stage two found nothing to read."""
    _part(tmp_path, images=False, words=False)
    assert not stage1.done(tmp_path, 1)
    assert not stage2.ready(tmp_path / "part0001")


def test_one_image_missing_its_words_is_not_prepared(tmp_path: Path) -> None:
    """Every named composite must be readable, not just one of them."""
    part = _part(tmp_path, images=False, words=True)
    (part / "composite001.words.json").unlink()
    assert not stage1.done(tmp_path, 1)
    assert not stage2.ready(part)


def test_a_cached_summary_page_is_not_counted_as_bought(tmp_path: Path) -> None:
    """``images_billed`` is what the run is charged for, so a free read must not appear in it.

    The count decides the reported Vision cost per constituency. Incrementing it on the cached
    path adds one phantom image per part -- invisible while every part was being read for the
    first time, and the whole state once the fleet resumes instead of re-rendering.
    """
    part = tmp_path / "part0001"
    part.mkdir()
    Image.new("L", (40, 20), 255).save(part / "summary_page.png")
    (part / "summary_page.words.json").write_text("[]", encoding="utf-8")

    def never(_images):
        raise AssertionError("Vision was called for a page whose words are already cached")

    _, paid = stage2.summary_from_vision(part, "", never)
    assert paid is False


def test_composites_are_named_from_the_record(tmp_path: Path) -> None:
    """Both images are named even with nothing on disk, or stage two reads half the part."""
    part = _part(tmp_path, images=False, words=True)
    assert [p.name for p in stage2.composites(part)] == sorted(PLACEMENTS)


def test_a_corrupt_rows_cache_is_treated_as_absent(tmp_path: Path) -> None:
    """AC20 died on one unreadable cache file out of 242 parts.

    The words the rows were parsed from are still there, so nothing is lost by parsing again --
    and a whole constituency is lost by refusing to.
    """
    from electors import run

    cache = tmp_path / "rows.jsonl"
    cache.write_text("\x00 not json at all\n", encoding="utf-8")
    assert run._cached_rows(cache) is None
    assert not cache.exists()


def test_a_good_rows_cache_is_read(tmp_path: Path) -> None:
    from electors import run

    cache = tmp_path / "rows.jsonl"
    cache.write_text('{"name": "a"}\n\n{"name": "b"}\n', encoding="utf-8")
    assert run._cached_rows(cache) == [{"name": "a"}, {"name": "b"}]
    assert cache.exists()
